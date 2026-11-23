"""Shared pytest fixtures.

``ZTMStaticSchedule`` instances own independent feeds. Only ``ZTMService`` is
a singleton; reset it around each test to isolate its repository and worker.
"""

from __future__ import annotations

import copy
import gc

import pytest

from services.ztm_service import ZTMService
from services.ztm_static_schedule import ZTMStaticSchedule


@pytest.fixture(autouse=True)
def fresh_service():
    """Give every test its own service and stop any worker it starts."""
    ZTMService._instance = None
    yield
    if ZTMService._instance is not None:
        ZTMService._instance.stop_daily_refresh()
    ZTMService._instance = None
    gc.unfreeze()


@pytest.fixture(scope="session")
def real_gtfs() -> dict:
    """The full bundled mock dataset, parsed exactly once for the whole run.

    Parsing the ~900k-row mock zip is expensive, so tests that only need to
    inspect the *result* of ``get_static_gtfs`` share this cached copy instead of
    re-parsing. Tests that exercise the loading itself call the service directly.
    Session scope means this is unaffected by the per-test singleton reset.
    """
    service = ZTMService()
    with service._mock_gtfs_zip() as archive:
        return {
            name: service._read_csv_from_zip(archive, f"{name}.txt")
            for name in (
                "stops",
                "routes",
                "stop_times",
                "trips",
                "calendar",
                "feed_info",
                "calendar_dates",
            )
        }


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


@pytest.fixture
def sample_schedule(sample_gtfs):
    return ZTMStaticSchedule.load(sample_gtfs)


@pytest.fixture
def search_service(sample_schedule):
    from services.ztm_connection_search import (
        ZTMConnectionSearchService,
    )

    return ZTMConnectionSearchService(schedule=sample_schedule)


@pytest.fixture
def multi_transfer_gtfs(sample_gtfs) -> dict:
    """Extends ``sample_gtfs`` with:
    - S4 + Route R3 + Trip T4 (S3 06:25 -> S4 06:35) for 2-transfer journeys
    - Trip T_SLOW (S1 05:35 -> S3 06:45) for Pareto / target-cutoff tests
    - Trip T_NO_DROP (S1 05:38 -> S2 [drop_off=1] -> S3 06:30) for drop_off_type=1 tests
    """
    gtfs = copy.deepcopy(sample_gtfs)

    gtfs["stops"].append(
        {
            "stop_id": "S4",
            "stop_code": "004",
            "stop_name": "Pętla",
            "stop_lat": "52.43",
            "stop_lon": "16.96",
            "zone_id": "B",
        }
    )
    gtfs["routes"].append(
        {
            "route_id": "R3",
            "agency_id": "1",
            "route_short_name": "3",
            "route_long_name": "Pętla",
            "route_type": "3",
        }
    )
    gtfs["trips"].extend(
        [
            {
                "trip_id": "T4",
                "route_id": "R3",
                "service_id": "WKD",
                "trip_headsign": "Pętla",
                "direction_id": "0",
                "brigade": "",
            },
            {
                "trip_id": "T_SLOW",
                "route_id": "R1",
                "service_id": "WKD",
                "trip_headsign": "Dworzec",
                "direction_id": "0",
                "brigade": "",
            },
            {
                "trip_id": "T_NO_DROP",
                "route_id": "R1",
                "service_id": "WKD",
                "trip_headsign": "Dworzec",
                "direction_id": "0",
                "brigade": "",
            },
        ]
    )
    gtfs["stop_times"].extend(
        [
            {
                "trip_id": "T4",
                "stop_id": "S3",
                "arrival_time": "06:25:00",
                "departure_time": "06:25:00",
                "stop_sequence": "0",
                "stop_headsign": "Pętla",
                "pickup_type": "0",
                "drop_off_type": "0",
            },
            {
                "trip_id": "T4",
                "stop_id": "S4",
                "arrival_time": "06:35:00",
                "departure_time": "06:35:00",
                "stop_sequence": "1",
                "stop_headsign": "Pętla",
                "pickup_type": "0",
                "drop_off_type": "0",
            },
            {
                "trip_id": "T_SLOW",
                "stop_id": "S1",
                "arrival_time": "05:35:00",
                "departure_time": "05:35:00",
                "stop_sequence": "0",
                "stop_headsign": "Dworzec",
                "pickup_type": "0",
                "drop_off_type": "0",
            },
            {
                "trip_id": "T_SLOW",
                "stop_id": "S3",
                "arrival_time": "06:45:00",
                "departure_time": "06:45:00",
                "stop_sequence": "1",
                "stop_headsign": "Dworzec",
                "pickup_type": "0",
                "drop_off_type": "0",
            },
            {
                "trip_id": "T_NO_DROP",
                "stop_id": "S1",
                "arrival_time": "05:38:00",
                "departure_time": "05:38:00",
                "stop_sequence": "0",
                "stop_headsign": "Dworzec",
                "pickup_type": "0",
                "drop_off_type": "0",
            },
            {
                "trip_id": "T_NO_DROP",
                "stop_id": "S2",
                "arrival_time": "05:43:00",
                "departure_time": "05:43:00",
                "stop_sequence": "1",
                "stop_headsign": "Dworzec",
                "pickup_type": "0",
                "drop_off_type": "1",
            },
            {
                "trip_id": "T_NO_DROP",
                "stop_id": "S3",
                "arrival_time": "06:30:00",
                "departure_time": "06:30:00",
                "stop_sequence": "2",
                "stop_headsign": "Dworzec",
                "pickup_type": "0",
                "drop_off_type": "0",
            },
        ]
    )
    return gtfs


@pytest.fixture
def multi_transfer_schedule(multi_transfer_gtfs):
    return ZTMStaticSchedule.load(multi_transfer_gtfs)


@pytest.fixture
def multi_transfer_search_service(multi_transfer_schedule):
    from services.ztm_connection_search import (
        ZTMConnectionSearchService,
    )

    return ZTMConnectionSearchService(schedule=multi_transfer_schedule)
