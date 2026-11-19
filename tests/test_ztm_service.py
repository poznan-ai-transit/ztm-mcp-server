"""Tests for ZTMService: mock-GTFS loading, parsing, and scheduling math."""

from __future__ import annotations

import io
import zipfile
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime
from unittest.mock import Mock

import pytest

from ini import Ini
from services.ztm_schedule_repository import WARSAW, ScheduleEntry
from services.ztm_service import GTFSArchive, ZTMService
from services.ztm_static_schedule import ZTMStaticSchedule

# --------------------------------------------------------------------------- #
# Singleton behaviour
# --------------------------------------------------------------------------- #


def test_instance_returns_same_object():
    assert ZTMService.instance() is ZTMService.instance()


def test_constructor_and_instance_share_singleton():
    assert ZTMService() is ZTMService.instance()


# --------------------------------------------------------------------------- #
# get_static_gtfs — loads the bundled mock zip
# --------------------------------------------------------------------------- #


@pytest.fixture
def service() -> ZTMService:
    return ZTMService.instance()


def test_get_static_gtfs_has_expected_top_level_keys(real_gtfs):
    assert set(real_gtfs) == {
        "stops",
        "routes",
        "stop_times",
        "trips",
        "calendar",
        "feed_info",
        "calendar_dates",
    }


def test_get_static_gtfs_collections_are_non_empty(real_gtfs):
    assert (
        real_gtfs["stops"]
        and real_gtfs["routes"]
        and real_gtfs["stop_times"]
        and real_gtfs["trips"]
    )


def test_get_static_gtfs_rows_are_dicts_with_expected_fields(real_gtfs):
    assert "stop_id" in real_gtfs["stops"][0]
    assert "route_id" in real_gtfs["routes"][0]
    assert {"trip_id", "stop_id"} <= set(real_gtfs["stop_times"][0])


def test_get_static_gtfs_preserves_polish_diacritics(real_gtfs):
    """Mock files are UTF-8 (with BOM); diacritics must survive decoding."""
    blob = "".join(r.get("route_long_name", "") for r in real_gtfs["routes"])
    assert any(ch in blob for ch in "ąćęłńóśźż".upper() + "ąćęłńóśźż")


@pytest.mark.slow
def test_get_static_gtfs_is_deterministic(service, monkeypatch):
    """FR-5 / NFR-2: identical inputs yield identical outputs across runs."""
    monkeypatch.setattr(
        service, "_fetch_static_gtfs_zip", lambda *args, **kwargs: service._mock_gtfs_zip()
    )
    assert service.get_static_gtfs() == service.get_static_gtfs()


# --------------------------------------------------------------------------- #
# _read_csv_from_zip — CSV parsing with a UTF-8 BOM
# --------------------------------------------------------------------------- #


def _zip_with(name: str, content: str) -> zipfile.ZipFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, content)
    buf.seek(0)
    return zipfile.ZipFile(buf)


def test_read_csv_from_zip_returns_list_of_dicts(service):
    zf = _zip_with("stops.txt", "stop_id,stop_name\nS1,Rynek\nS2,Smolna\n")
    rows = service._read_csv_from_zip(zf, "stops.txt")
    assert rows == [
        {"stop_id": "S1", "stop_name": "Rynek"},
        {"stop_id": "S2", "stop_name": "Smolna"},
    ]


def test_read_csv_from_zip_strips_utf8_bom_from_header(service):
    """A leading BOM must not become part of the first column name."""
    zf = _zip_with("stops.txt", "﻿stop_id,stop_name\nS1,Rynek\n")
    rows = service._read_csv_from_zip(zf, "stops.txt")
    assert "stop_id" in rows[0]  # not "﻿stop_id"


def test_read_csv_from_zip_handles_empty_body(service):
    zf = _zip_with("calendar_dates.txt", "service_id,date,exception_type\n")
    assert service._read_csv_from_zip(zf, "calendar_dates.txt") == []


# --------------------------------------------------------------------------- #
# Daily refresh startup and scheduling
# --------------------------------------------------------------------------- #


def test_start_daily_refresh_loads_before_starting_worker(service, monkeypatch):
    thread = Mock()
    refresh = Mock()
    monkeypatch.setattr(service, "_refresh_schedules", refresh)
    monkeypatch.setattr("services.ztm_service.threading.Thread", Mock(return_value=thread))
    thread.start.side_effect = lambda: refresh.assert_called_once_with()

    service.start_daily_refresh()

    refresh.assert_called_once_with()
    thread.start.assert_called_once_with()


def test_start_daily_refresh_initial_failure_does_not_start_worker(service, monkeypatch):
    monkeypatch.setattr(service, "_refresh_schedules", Mock(side_effect=ValueError("No feeds")))
    thread_factory = Mock()
    monkeypatch.setattr("services.ztm_service.threading.Thread", thread_factory)

    with pytest.raises(ValueError, match="No feeds"):
        service.start_daily_refresh()

    thread_factory.assert_not_called()
    assert service._thread is None


def test_daily_refresh_worker_waits_after_initial_load(service, monkeypatch):
    refresh = Mock()
    thread_factory = Mock()
    monkeypatch.setattr(service, "_refresh_schedules", refresh)
    monkeypatch.setattr("services.ztm_service.threading.Thread", thread_factory)
    monkeypatch.setattr(service, "_seconds_until_next_refresh", Mock(return_value=3600))
    wait = Mock(side_effect=lambda **kwargs: service._stop_event.set())
    monkeypatch.setattr(service._stop_event, "wait", wait)

    service.start_daily_refresh()
    thread_factory.call_args.kwargs["target"]()

    wait.assert_called_once_with(timeout=3600)
    refresh.assert_called_once_with()


def test_seconds_until_six_am_before_six_same_day(service):
    now = datetime(2026, 6, 15, 5, tzinfo=WARSAW)
    assert service._seconds_until_next_refresh(now) == 3600


def test_seconds_until_six_am_after_six_rolls_to_next_day(service):
    now = datetime(2026, 6, 15, 7, tzinfo=WARSAW)
    assert service._seconds_until_next_refresh(now) == 23 * 3600


def test_seconds_until_six_am_exactly_six_rolls_to_next_day(service):
    """At 06:00 the next run is tomorrow, not now (boundary is `<=`)."""
    now = datetime(2026, 6, 15, 6, tzinfo=WARSAW)
    assert service._seconds_until_next_refresh(now) == 24 * 3600


def test_seconds_until_six_am_is_always_positive(service):
    for hour in range(24):
        now = datetime(2026, 6, 15, hour, 30, tzinfo=WARSAW)
        assert service._seconds_until_next_refresh(now) > 0


@pytest.mark.parametrize("month,day,hours", [(3, 29, 5), (10, 25, 7)])
def test_seconds_until_refresh_accounts_for_warsaw_dst(service, month, day, hours):
    now = datetime(2026, month, day, tzinfo=WARSAW)
    assert service._seconds_until_next_refresh(now) == hours * 3600


# --------------------------------------------------------------------------- #
# _mock_gtfs_zip — the bundled fixture archive
# --------------------------------------------------------------------------- #


def test_mock_gtfs_zip_contains_required_gtfs_files(service):
    with service._mock_gtfs_zip() as zf:
        names = set(zf.namelist())
    assert {"stops.txt", "routes.txt", "stop_times.txt"} <= names


def test_fetch_static_gtfs_uses_configuration(service, monkeypatch):
    monkeypatch.setattr(
        "services.ztm_service.ini",
        Ini(gtfs_endpoint="https://example.test/gtfs", request_timeout_seconds=12),
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("stops.txt", "stop_id,stop_name\n")
    response = Mock(content=buf.getvalue())
    get = Mock(return_value=response)
    monkeypatch.setattr("services.ztm_service.requests.get", get)

    with service._fetch_static_gtfs_zip() as archive:
        assert "stops.txt" in archive.namelist()
    get.assert_called_once_with("https://example.test/gtfs", timeout=12)
    response.raise_for_status.assert_called_once_with()

    with service._fetch_static_gtfs_zip("https://example.test/override"):
        pass
    get.assert_called_with("https://example.test/override", timeout=12)

    with service._fetch_static_gtfs_zip(filename="20260601_20260630.zip"):
        pass
    get.assert_called_with(
        "https://example.test/gtfs", timeout=12, params={"file": "20260601_20260630.zip"}
    )


@pytest.fixture
def published_archive(service, sample_gtfs, monkeypatch):
    archive = GTFSArchive(
        "20260601_20260630.zip",
        date(2026, 6, 1),
        date(2026, 6, 30),
        datetime(2026, 6, 1, tzinfo=WARSAW),
    )
    service.repository.replace(
        [ScheduleEntry(archive.filename, archive.modified_at, ZTMStaticSchedule.load(sample_gtfs))]
    )
    monkeypatch.setattr(service, "list_gtfs_archives", Mock(return_value=[archive]))
    monkeypatch.setattr(service, "get_static_gtfs", Mock(return_value=sample_gtfs))
    return archive


def test_refresh_reuses_unchanged_feed(service, published_archive):
    previous = service.repository.for_date(date(2026, 6, 15))

    service._refresh_schedules(today=date(2026, 6, 15))

    service.get_static_gtfs.assert_not_called()
    assert service.repository.for_date(date(2026, 6, 15)) is previous


def test_refresh_reloads_modified_feed_without_mutating_pinned_schedule(
    service, published_archive, sample_gtfs
):
    repository = service.repository
    pinned = repository.for_date(date(2026, 6, 15))
    sample_gtfs["stops"][0]["stop_name"] = "Updated stop"
    updated = replace(published_archive, modified_at=datetime(2026, 6, 2, tzinfo=WARSAW))
    service.list_gtfs_archives.return_value = [updated]

    service._refresh_schedules(today=date(2026, 6, 15))

    service.get_static_gtfs.assert_called_once_with(published_archive.filename)
    assert service.repository is repository
    assert repository.for_date(date(2026, 6, 15)).get_stop("S1").stop_name == "Updated stop"
    assert pinned.get_stop("S1").stop_name == "Rynek"


def test_refresh_keeps_future_feed_and_prunes_expired_feed(service, published_archive, sample_gtfs):
    expired = replace(
        published_archive,
        filename="20260501_20260531.zip",
        start_date=date(2026, 5, 1),
        end_date=date(2026, 5, 31),
    )
    old_rows = deepcopy(sample_gtfs)
    old_rows["feed_info"][0].update(feed_start_date="20260501", feed_end_date="20260531")
    service.repository.replace(
        [
            *service.repository.list_schedules(),
            ScheduleEntry(expired.filename, expired.modified_at, ZTMStaticSchedule.load(old_rows)),
        ]
    )
    future = replace(
        published_archive,
        filename="20260701_20260731.zip",
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 31),
    )
    future_rows = deepcopy(sample_gtfs)
    future_rows["feed_info"][0].update(feed_start_date="20260701", feed_end_date="20260731")
    service.list_gtfs_archives.return_value = [expired, published_archive, future]
    service.get_static_gtfs.return_value = future_rows

    service._refresh_schedules(today=date(2026, 6, 15))

    service.get_static_gtfs.assert_called_once_with(future.filename)
    assert {entry.filename for entry in service.repository.list_schedules()} == {
        published_archive.filename,
        future.filename,
    }
    assert service.repository.for_date(date(2026, 7, 15)).available_date_range == (
        date(2026, 7, 1),
        date(2026, 7, 31),
    )


@pytest.mark.parametrize("failure", ["download", "coverage_gap"])
def test_failed_refresh_preserves_published_repository(
    service, published_archive, sample_gtfs, failure
):
    previous = service.repository.list_schedules()
    updated = replace(published_archive, modified_at=datetime(2026, 6, 2, tzinfo=WARSAW))
    service.list_gtfs_archives.return_value = [updated]
    if failure == "download":
        service.get_static_gtfs.side_effect = ValueError("Download failed")
    else:
        # Archive filename suggests coverage; actual GTFS metadata leaves today uncovered.
        sample_gtfs["feed_info"][0]["feed_end_date"] = "20260614"

    with pytest.raises(ValueError):
        service._refresh_schedules(today=date(2026, 6, 15))

    assert service.repository.list_schedules() is previous
    assert service.repository.for_date(date(2026, 6, 15)) is previous[0].schedule
