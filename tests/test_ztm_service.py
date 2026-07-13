"""Tests for ZTMService: mock-GTFS loading, index building, and scheduling math."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime

import pytest

from services.ztm_service import ZTMService

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
    assert set(real_gtfs) == {"stops", "routes", "stop_times", "indexes"}


def test_get_static_gtfs_collections_are_non_empty(real_gtfs):
    assert real_gtfs["stops"] and real_gtfs["routes"] and real_gtfs["stop_times"]


def test_get_static_gtfs_rows_are_dicts_with_expected_fields(real_gtfs):
    assert "stop_id" in real_gtfs["stops"][0]
    assert "route_id" in real_gtfs["routes"][0]
    assert {"trip_id", "stop_id"} <= set(real_gtfs["stop_times"][0])


def test_get_static_gtfs_preserves_polish_diacritics(real_gtfs):
    """Mock files are UTF-8 (with BOM); diacritics must survive decoding."""
    blob = "".join(r.get("route_long_name", "") for r in real_gtfs["routes"])
    assert any(ch in blob for ch in "ąćęłńóśźż".upper() + "ąćęłńóśźż")


@pytest.mark.slow
def test_get_static_gtfs_is_deterministic(service):
    """FR-5 / NFR-2: identical inputs yield identical outputs across runs."""
    assert service.get_static_gtfs() == service.get_static_gtfs()


# --------------------------------------------------------------------------- #
# _build_indexes — grouping correctness on hand-checkable input
# --------------------------------------------------------------------------- #


def test_build_indexes_keys_by_id(service, sample_gtfs):
    idx = service._build_indexes(
        sample_gtfs["stops"], sample_gtfs["routes"], sample_gtfs["stop_times"]
    )
    assert set(idx["stops_by_id"]) == {"S1", "S2"}
    assert set(idx["routes_by_id"]) == {"R1", "R2"}
    assert idx["stops_by_id"]["S1"]["stop_name"] == "Rynek"


def test_build_indexes_groups_stop_times_by_trip(service, sample_gtfs):
    idx = service._build_indexes(
        sample_gtfs["stops"], sample_gtfs["routes"], sample_gtfs["stop_times"]
    )
    assert len(idx["stop_times_by_trip_id"]["T1"]) == 2
    assert len(idx["stop_times_by_trip_id"]["T2"]) == 1


def test_build_indexes_groups_stop_times_by_stop(service, sample_gtfs):
    idx = service._build_indexes(
        sample_gtfs["stops"], sample_gtfs["routes"], sample_gtfs["stop_times"]
    )
    # S1 served by T1 and T2; S2 served only by T1.
    assert len(idx["stop_times_by_stop_id"]["S1"]) == 2
    assert len(idx["stop_times_by_stop_id"]["S2"]) == 1


def test_build_indexes_preserves_row_order_within_group(service, sample_gtfs):
    idx = service._build_indexes(
        sample_gtfs["stops"], sample_gtfs["routes"], sample_gtfs["stop_times"]
    )
    seqs = [r["stop_sequence"] for r in idx["stop_times_by_trip_id"]["T1"]]
    assert seqs == ["0", "1"]


def test_build_indexes_on_real_mock_data_covers_every_row(real_gtfs):
    data = real_gtfs
    idx = data["indexes"]
    # Every stop_time row is reachable through both trip and stop indexes.
    assert sum(len(v) for v in idx["stop_times_by_trip_id"].values()) == len(data["stop_times"])
    assert sum(len(v) for v in idx["stop_times_by_stop_id"].values()) == len(data["stop_times"])


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
# _seconds_until_next_six_am — daily refresh scheduling
# --------------------------------------------------------------------------- #


def test_seconds_until_six_am_before_six_same_day(service):
    now = datetime(2026, 6, 15, 5, 0, 0)  # 05:00 -> 1 hour away
    assert service._seconds_until_next_six_am(now) == 3600


def test_seconds_until_six_am_after_six_rolls_to_next_day(service):
    now = datetime(2026, 6, 15, 7, 0, 0)  # 07:00 -> 23 hours away
    assert service._seconds_until_next_six_am(now) == 23 * 3600


def test_seconds_until_six_am_exactly_six_rolls_to_next_day(service):
    """At 06:00 the next run is tomorrow, not now (boundary is `<=`)."""
    now = datetime(2026, 6, 15, 6, 0, 0)
    assert service._seconds_until_next_six_am(now) == 24 * 3600


def test_seconds_until_six_am_is_always_positive(service):
    for hour in range(24):
        now = datetime(2026, 6, 15, hour, 30, 0)
        assert service._seconds_until_next_six_am(now) > 0


# --------------------------------------------------------------------------- #
# _mock_gtfs_zip — the bundled fixture archive
# --------------------------------------------------------------------------- #


def test_mock_gtfs_zip_contains_required_gtfs_files(service):
    with service._mock_gtfs_zip() as zf:
        names = set(zf.namelist())
    assert {"stops.txt", "routes.txt", "stop_times.txt"} <= names
