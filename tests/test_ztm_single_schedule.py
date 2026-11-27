"""Development schedule selection, publication, and background refresh policy."""

import csv
import io
from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime, timedelta
from unittest.mock import Mock
from zipfile import BadZipFile, ZipFile

import pytest

import services.ztm_service as service_module
from services.ztm_schedule_repository import WARSAW
from services.ztm_service import GTFSArchive, ZTMService


@pytest.fixture
def feeds(sample_gtfs):
    archives = [
        GTFSArchive(
            "20260501_20260531.zip",
            date(2026, 5, 1),
            date(2026, 5, 31),
            datetime(2026, 5, 1, tzinfo=WARSAW),
        ),
        GTFSArchive(
            "20260601_20260630.zip",
            date(2026, 6, 1),
            date(2026, 6, 30),
            datetime(2026, 6, 1, tzinfo=WARSAW),
        ),
        GTFSArchive(
            "20260610_20260630.zip",
            date(2026, 6, 10),
            date(2026, 6, 30),
            datetime(2026, 6, 2, tzinfo=WARSAW),
        ),
        GTFSArchive(
            "20260701_20260731.zip",
            date(2026, 7, 1),
            date(2026, 7, 31),
            datetime(2026, 6, 3, tzinfo=WARSAW),
        ),
    ]
    rows = {}
    for archive in archives:
        data = deepcopy(sample_gtfs)
        data["feed_info"][0].update(
            feed_start_date=archive.start_date.strftime("%Y%m%d"),
            feed_end_date=archive.end_date.strftime("%Y%m%d"),
        )
        rows[archive.filename] = data
    return archives, rows


@pytest.fixture
def make_service(monkeypatch, feeds):
    archives, rows = feeds

    def create(**kwargs):
        ZTMService.initialize(**kwargs)
        service = ZTMService.instance()
        monkeypatch.setattr("services.ztm_service.warsaw_today", lambda: date(2026, 6, 15))
        monkeypatch.setattr(service, "list_gtfs_archives", Mock(return_value=archives))
        monkeypatch.setattr(service, "get_static_gtfs", Mock(side_effect=rows.__getitem__))
        monkeypatch.setattr("services.ztm_service.threading.Thread", Mock())
        return service

    return create


@pytest.mark.parametrize(
    ("today", "filename"),
    [
        (date(2026, 6, 1), "20260601_20260630.zip"),
        (date(2026, 6, 10), "20260610_20260630.zip"),
        (date(2026, 6, 15), "20260610_20260630.zip"),
        (date(2026, 6, 30), "20260610_20260630.zip"),
    ],
)
def test_auto_selects_latest_current_archive_before_downloading(
    make_service, monkeypatch, today, filename
):
    service = make_service(schedule_url="")
    monkeypatch.setattr("services.ztm_service.warsaw_today", lambda: today)

    service.start_daily_refresh()

    service.get_static_gtfs.assert_called_once_with(filename)
    entries = service.repository.list_schedules()
    assert [entry.filename for entry in entries] == [filename]
    assert service.repository.for_date(today) is entries[0].schedule


@pytest.mark.parametrize("reverse", [False, True])
def test_auto_breaks_modification_ties_by_filename(make_service, feeds, reverse):
    service = make_service(schedule_url="")
    archives, _ = feeds
    archives[1] = replace(archives[1], modified_at=archives[2].modified_at)
    if reverse:
        archives.reverse()

    service.start_daily_refresh()

    service.get_static_gtfs.assert_called_once_with("20260610_20260630.zip")


@pytest.mark.parametrize("filename", ["20260501_20260531.zip", "20260701_20260731.zip"])
def test_url_allows_historical_and_future_feeds_without_archive_listing(
    make_service, feeds, filename, tmp_path, monkeypatch
):
    archives, rows = feeds
    archive = next(item for item in archives if item.filename == filename)
    url = "https://example.test/download?feed=custom"
    service = make_service(schedule_url=url)
    path = tmp_path / "feed.zip"
    _write_gtfs(path, rows[filename])
    get = Mock(return_value=Mock(content=path.read_bytes()))
    monkeypatch.setattr("services.ztm_service.requests.get", get)

    service.start_daily_refresh()

    get.assert_called_once_with(url, timeout=service_module.ini.request_timeout_seconds)
    service.get_static_gtfs.assert_not_called()
    service.list_gtfs_archives.assert_not_called()
    entries = service.repository.list_schedules()
    assert [entry.filename for entry in entries] == [url]
    assert service.repository.for_date(archive.start_date) is entries[0].schedule
    with pytest.raises(ValueError, match="No GTFS schedule covers"):
        service.repository.for_date(date(2026, 6, 15))


def test_service_rejects_both_explicit_sources(tmp_path):
    with pytest.raises(ValueError, match="Choose either a schedule URL or a local file"):
        ZTMService.initialize(
            schedule_url="https://example.test/feed", schedule_file=tmp_path / "feed.zip"
        )


def test_auto_fails_when_no_archive_covers_today(make_service, feeds):
    service = make_service(schedule_url="")
    archives, _ = feeds
    service.list_gtfs_archives.return_value = [archives[0], archives[-1]]

    with pytest.raises(ValueError, match="No GTFS archive covers 2026-06-15"):
        service.start_daily_refresh()

    service.get_static_gtfs.assert_not_called()
    assert service._thread is None


@pytest.mark.parametrize("failure", ["download", "coverage", "missing_archive"])
def test_failed_single_refresh_keeps_published_repository(make_service, feeds, failure):
    service = make_service(schedule_url="")
    archives, rows = feeds
    service.start_daily_refresh()
    previous = service.repository.list_schedules()
    archives[2] = replace(archives[2], modified_at=archives[2].modified_at + timedelta(days=1))
    if failure == "download":
        service.get_static_gtfs.side_effect = ValueError("Download failed")
    elif failure == "coverage":
        rows[archives[2].filename]["feed_info"][0]["feed_end_date"] = "20260614"
    else:
        service.list_gtfs_archives.return_value = [archives[-1]]

    with pytest.raises(ValueError):
        service._refresh_single_schedule()

    assert service.repository.list_schedules() is previous
    assert service.repository.for_date(date(2026, 6, 15)) is previous[0].schedule


def test_background_refresh_follows_today(make_service, monkeypatch):
    service = make_service(schedule_url="")
    service.start_daily_refresh()
    monkeypatch.setattr("services.ztm_service.warsaw_today", lambda: date(2026, 7, 1))
    monkeypatch.setattr(service, "_seconds_until_next_refresh", Mock(return_value=60))

    def stop_after_refresh(**kwargs):
        if wait.call_count == 2:
            service._stop_event.set()

    wait = Mock(side_effect=stop_after_refresh)
    monkeypatch.setattr(service._stop_event, "wait", wait)
    service_module.threading.Thread.call_args.kwargs["target"]()

    assert [entry.filename for entry in service.repository.list_schedules()] == [
        "20260701_20260731.zip"
    ]
    assert wait.call_count == 2
    assert service.get_static_gtfs.call_count == 2


def test_default_startup_keeps_recent_and_future_schedules(make_service):
    service = make_service()
    service.start_daily_refresh()

    assert {entry.filename for entry in service.repository.list_schedules()} == {
        "20260601_20260630.zip",
        "20260610_20260630.zip",
        "20260701_20260731.zip",
    }
    assert service.get_static_gtfs.call_count == 3


def _write_gtfs(path, rows):
    with ZipFile(path, "w") as archive:
        for name, data in rows.items():
            content = io.StringIO()
            if data:
                writer = csv.DictWriter(content, fieldnames=list(data[0]))
                writer.writeheader()
                writer.writerows(data)
            archive.writestr(f"{name}.txt", content.getvalue())


@pytest.fixture
def make_explicit_service(monkeypatch):
    def create(**kwargs):
        ZTMService.initialize(**kwargs)
        service = ZTMService.instance()
        monkeypatch.setattr(
            service, "list_gtfs_archives", Mock(side_effect=AssertionError("No listing"))
        )
        monkeypatch.setattr("services.ztm_service.threading.Thread", Mock())
        monkeypatch.setattr("services.ztm_service.warsaw_today", lambda: date(2026, 11, 25))
        return service

    return create


def test_local_zip_accepts_arbitrary_name_and_relative_path(
    make_explicit_service, sample_gtfs, tmp_path, monkeypatch
):
    path = tmp_path / "my schedules" / "custom feed.data"
    path.parent.mkdir()
    _write_gtfs(path, sample_gtfs)
    monkeypatch.chdir(tmp_path)
    get = Mock(side_effect=AssertionError("Local schedule must not use HTTP"))
    monkeypatch.setattr("services.ztm_service.requests.get", get)

    explicit_service = make_explicit_service(schedule_file=path.relative_to(tmp_path))
    explicit_service.start_daily_refresh()

    entries = explicit_service.repository.list_schedules()
    assert [entry.filename for entry in entries] == [str(path)]
    schedule = explicit_service.repository.for_date(date(2026, 6, 15))
    assert schedule.get_stop("S1").stop_name == "Rynek"
    assert schedule.data.connections_by_service_day
    get.assert_not_called()


def test_url_download_uses_exact_url_without_archive_lookup(
    make_explicit_service, sample_gtfs, tmp_path, monkeypatch
):
    path = tmp_path / "feed.zip"
    _write_gtfs(path, sample_gtfs)
    response = Mock(content=path.read_bytes())
    get = Mock(return_value=response)
    monkeypatch.setattr("services.ztm_service.requests.get", get)
    url = "https://example.test/download?feed=custom&version=1"

    explicit_service = make_explicit_service(schedule_url=url)
    explicit_service.start_daily_refresh()

    get.assert_called_once_with(url, timeout=service_module.ini.request_timeout_seconds)
    response.raise_for_status.assert_called_once_with()
    assert [entry.filename for entry in explicit_service.repository.list_schedules()] == [url]
    assert explicit_service.repository.for_date(date(2026, 6, 15)).get_stop("S1") is not None


@pytest.mark.parametrize("failure", ["missing", "invalid_zip", "missing_gtfs"])
def test_invalid_local_source_fails_before_worker_start(make_explicit_service, tmp_path, failure):
    path = tmp_path / "feed.zip"
    error = FileNotFoundError
    if failure == "invalid_zip":
        path.write_text("not a zip", encoding="utf-8")
        error = BadZipFile
    elif failure == "missing_gtfs":
        with ZipFile(path, "w") as archive:
            archive.writestr("unrelated.txt", "no GTFS data")

    explicit_service = make_explicit_service(schedule_file=path)
    with pytest.raises(error):
        explicit_service.start_daily_refresh()

    assert explicit_service.repository.list_schedules() == ()
    assert explicit_service._thread is None


@pytest.mark.parametrize("source", ["url", "file"])
def test_explicit_refresh_keeps_source_and_preserves_repository_on_failure(
    make_explicit_service, sample_gtfs, tmp_path, monkeypatch, source
):
    path = tmp_path / "my feed.zip"
    _write_gtfs(path, sample_gtfs)
    url = "https://example.test/download"
    get = Mock(return_value=Mock(content=path.read_bytes()))
    monkeypatch.setattr("services.ztm_service.requests.get", get)
    options = {"schedule_url": url} if source == "url" else {"schedule_file": path}
    explicit_service = make_explicit_service(**options)
    explicit_service.start_daily_refresh()
    original = explicit_service.repository.for_date(date(2026, 6, 15))
    updated = deepcopy(sample_gtfs)
    updated["stops"][0]["stop_name"] = "Updated stop"
    _write_gtfs(path, updated)
    get.return_value.content = path.read_bytes()

    explicit_service._refresh_single_schedule()

    current = explicit_service.repository.for_date(date(2026, 6, 15))
    assert current.get_stop("S1").stop_name == "Updated stop"
    assert original.get_stop("S1").stop_name == "Rynek"
    entries = explicit_service.repository.list_schedules()
    assert [entry.filename for entry in entries] == [url if source == "url" else str(path)]
    if source == "url":
        assert get.call_count == 2
        get.side_effect = ValueError("Download failed")
    else:
        get.assert_not_called()
        path.write_text("corrupted zip", encoding="utf-8")

    with pytest.raises((ValueError, BadZipFile)):
        explicit_service._refresh_single_schedule()

    assert explicit_service.repository.list_schedules() is entries
