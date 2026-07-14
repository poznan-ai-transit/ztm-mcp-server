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
    """A tiny, hand-checkable raw GTFS feed in the shape ZTMService produces.

    It includes enough rows for ``ZTMStaticSchedule.load`` to build route/stop
    indexes, stop-time lookups, and the service calendar.
    """
    stops = [
        {
            "stop_id": "S1",
            "stop_code": "001",
            "stop_name": "Rynek",
            "stop_lat": "52.40",
            "stop_lon": "16.93",
            "zone_id": "A",
        },
        {
            "stop_id": "S2",
            "stop_code": "002",
            "stop_name": "Żabinko",
            "stop_lat": "52.41",
            "stop_lon": "16.94",
            "zone_id": "A",
        },
        {
            "stop_id": "S3",
            "stop_code": "003",
            "stop_name": "Dworzec",
            "stop_lat": "52.42",
            "stop_lon": "16.95",
            "zone_id": "B",
        },
    ]
    routes = [
        {
            "route_id": "R1",
            "agency_id": "1",
            "route_short_name": "1",
            "route_long_name": "Łęczyca",
            "route_type": "3",
        },
        {
            "route_id": "R2",
            "agency_id": "1",
            "route_short_name": "2",
            "route_long_name": "Śrem",
            "route_type": "3",
        },
    ]
    trips = [
        {
            "trip_id": "T1",
            "route_id": "R1",
            "service_id": "WKD",
            "trip_headsign": "Rynek",
            "direction_id": "0",
            "brigade": "",
        },
        {
            "trip_id": "T2",
            "route_id": "R1",
            "service_id": "WKD",
            "trip_headsign": "Rynek",
            "direction_id": "0",
            "brigade": "",
        },
        {
            "trip_id": "T3",
            "route_id": "R2",
            "service_id": "WKD",
            "trip_headsign": "Dworzec",
            "direction_id": "1",
            "brigade": "",
        },
    ]
    stop_times = [
        {
            "trip_id": "T1",
            "stop_id": "S1",
            "arrival_time": "05:38:00",
            "departure_time": "05:38:00",
            "stop_sequence": "0",
            "stop_headsign": "Rynek",
            "pickup_type": "0",
            "drop_off_type": "0",
        },
        {
            "trip_id": "T1",
            "stop_id": "S2",
            "arrival_time": "05:43:00",
            "departure_time": "05:43:00",
            "stop_sequence": "1",
            "stop_headsign": "Rynek",
            "pickup_type": "0",
            "drop_off_type": "0",
        },
        {
            "trip_id": "T2",
            "stop_id": "S1",
            "arrival_time": "25:30:00",
            "departure_time": "25:30:00",
            "stop_sequence": "0",
            "stop_headsign": "Rynek",
            "pickup_type": "0",
            "drop_off_type": "0",
        },
        {
            "trip_id": "T2",
            "stop_id": "S2",
            "arrival_time": "25:35:00",
            "departure_time": "25:35:00",
            "stop_sequence": "1",
            "stop_headsign": "Rynek",
            "pickup_type": "0",
            "drop_off_type": "0",
        },
        {
            "trip_id": "T3",
            "stop_id": "S2",
            "arrival_time": "06:10:00",
            "departure_time": "06:10:00",
            "stop_sequence": "0",
            "stop_headsign": "Dworzec",
            "pickup_type": "0",
            "drop_off_type": "0",
        },
        {
            "trip_id": "T3",
            "stop_id": "S3",
            "arrival_time": "06:20:00",
            "departure_time": "06:20:00",
            "stop_sequence": "1",
            "stop_headsign": "Dworzec",
            "pickup_type": "0",
            "drop_off_type": "0",
        },
    ]
    calendar = [
        {
            "service_id": "WKD",
            "monday": "1",
            "tuesday": "1",
            "wednesday": "1",
            "thursday": "1",
            "friday": "1",
            "saturday": "0",
            "sunday": "0",
            "start_date": "20260601",
            "end_date": "20260630",
        }
    ]
    return {
        "stops": stops,
        "routes": routes,
        "trips": trips,
        "stop_times": stop_times,
        "calendar": calendar,
        "feed_info": [
            {
                "feed_publisher_name": "ZTM Poznań",
                "feed_publisher_url": "https://www.ztm.poznan.pl/",
                "feed_lang": "pl",
                "feed_start_date": "20260601",
                "feed_end_date": "20260630",
            }
        ],
        "calendar_dates": [],
    }
