"""Shared pytest fixtures.

Both ``ZTMStaticSchedule`` and ``ZTMService`` are process-wide singletons that
cache their instance on a class attribute. Without isolation, data written by one
test would leak into the next and break determinism. The autouse fixture below
resets the cached instances around every test so each one starts clean.
"""

from __future__ import annotations

import pytest

from services.ztm_service import ZTMService
from services.ztm_static_schedule import ZTMStaticSchedule


def _reset_singletons() -> None:
    ZTMStaticSchedule._instance = None
    ZTMService._instance = None


@pytest.fixture(autouse=True)
def fresh_singletons():
    """Give every test its own ``ZTMStaticSchedule`` / ``ZTMService`` instance."""
    _reset_singletons()
    yield
    _reset_singletons()


@pytest.fixture(scope="session")
def real_gtfs() -> dict:
    """The full bundled mock dataset, parsed exactly once for the whole run.

    Parsing the ~900k-row mock zip is expensive, so tests that only need to
    inspect the *result* of ``get_static_gtfs`` share this cached copy instead of
    re-parsing. Tests that exercise the loading itself call the service directly.
    Session scope means this is unaffected by the per-test singleton reset.
    """
    return ZTMService().get_static_gtfs()


@pytest.fixture
def sample_gtfs() -> dict:
    """A tiny, hand-checkable GTFS dataset in the shape ZTMService produces.

    Two stops, two routes, and stop_times for two trips. The numbers are chosen
    so index grouping is easy to assert by hand:
      - trip ``T1`` visits stops ``S1`` then ``S2``
      - trip ``T2`` visits stop ``S1`` only
      - stop ``S1`` therefore appears in two trips, ``S2`` in one.
    """
    stops = [
        {"stop_id": "S1", "stop_name": "Rynek", "stop_lat": "52.40", "stop_lon": "16.93"},
        {"stop_id": "S2", "stop_name": "Żabinko", "stop_lat": "52.41", "stop_lon": "16.94"},
    ]
    routes = [
        {"route_id": "R1", "route_short_name": "1", "route_long_name": "Łęczyca"},
        {"route_id": "R2", "route_short_name": "2", "route_long_name": "Śrem"},
    ]
    stop_times = [
        {"trip_id": "T1", "stop_id": "S1", "stop_sequence": "0", "departure_time": "05:38:00"},
        {"trip_id": "T1", "stop_id": "S2", "stop_sequence": "1", "departure_time": "05:43:00"},
        {"trip_id": "T2", "stop_id": "S1", "stop_sequence": "0", "departure_time": "25:30:00"},
    ]
    indexes = {
        "stops_by_id": {row["stop_id"]: row for row in stops},
        "routes_by_id": {row["route_id"]: row for row in routes},
        "stop_times_by_trip_id": {
            "T1": [r for r in stop_times if r["trip_id"] == "T1"],
            "T2": [r for r in stop_times if r["trip_id"] == "T2"],
        },
        "stop_times_by_stop_id": {
            "S1": [r for r in stop_times if r["stop_id"] == "S1"],
            "S2": [r for r in stop_times if r["stop_id"] == "S2"],
        },
    }
    return {"stops": stops, "routes": routes, "stop_times": stop_times, "indexes": indexes}
