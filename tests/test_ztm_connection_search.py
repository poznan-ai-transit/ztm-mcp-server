from datetime import datetime

import pytest

from services.ztm_connection_search import (
    ConnectionSearchRequest,
)


def test_direct_connection(search_service):
    request = ConnectionSearchRequest(
        from_stop_id="S1",
        to_stop_id="S2",
        departure_time=datetime(2026, 6, 1, 5, 30),
        max_transfers=0,
    )

    results = search_service.search_connections(request)

    assert len(results) == 1

    journey = results[0]

    assert journey.transfers == 0
    assert len(journey.legs) == 1
    assert journey.legs[0].trip_id == "T1"
    assert journey.legs[0].departure_time == datetime(2026, 6, 1, 5, 38)
    assert journey.legs[0].arrival_time == datetime(2026, 6, 1, 5, 43)


def test_trip_departure_already_passed(search_service):
    request = ConnectionSearchRequest(
        from_stop_id="S1",
        to_stop_id="S2",
        departure_time=datetime(2026, 6, 1, 5, 39),
        max_transfers=0,
    )

    results = search_service.search_connections(request)

    # T1 has already left. T2 is an overnight trip at 25:30.
    assert all(leg.trip_id != "T1" for journey in results for leg in journey.legs)


def test_direct_connection_not_found_when_transfers_disabled(
    search_service,
):
    request = ConnectionSearchRequest(
        from_stop_id="S1",
        to_stop_id="S3",
        departure_time=datetime(2026, 6, 1, 5, 30),
        max_transfers=0,
    )

    results = search_service.search_connections(request)

    assert results == []


def test_connection_with_one_transfer(search_service):
    request = ConnectionSearchRequest(
        from_stop_id="S1",
        to_stop_id="S3",
        departure_time=datetime(2026, 6, 1, 5, 30),
        max_transfers=1,
    )

    results = search_service.search_connections(request)

    assert results

    journey = results[0]

    assert journey.transfers == 1
    assert len(journey.legs) == 2

    assert journey.legs[0].trip_id == "T1"
    assert journey.legs[0].from_stop_id == "S1"
    assert journey.legs[0].to_stop_id == "S2"

    assert journey.legs[1].trip_id == "T3"
    assert journey.legs[1].from_stop_id == "S2"
    assert journey.legs[1].to_stop_id == "S3"

    assert journey.legs[0].arrival_time <= journey.legs[1].departure_time


def test_unknown_origin_stop(search_service):
    request = ConnectionSearchRequest(
        from_stop_id="UNKNOWN",
        to_stop_id="S2",
        departure_time=datetime(2026, 6, 1, 5, 30),
    )

    with pytest.raises(ValueError, match="Unknown origin"):
        search_service.search_connections(request)


def test_unknown_destination_stop(search_service):
    request = ConnectionSearchRequest(
        from_stop_id="S1",
        to_stop_id="UNKNOWN",
        departure_time=datetime(2026, 6, 1, 5, 30),
    )

    with pytest.raises(ValueError, match="Unknown destination"):
        search_service.search_connections(request)


def test_same_origin_and_destination(search_service):
    request = ConnectionSearchRequest(
        from_stop_id="S1",
        to_stop_id="S1",
        departure_time=datetime(2026, 6, 1, 5, 30),
    )

    assert search_service.search_connections(request) == []


def test_overnight_trip(search_service):
    request = ConnectionSearchRequest(
        from_stop_id="S1",
        to_stop_id="S2",
        departure_time=datetime(2026, 6, 2, 1, 0),
        max_transfers=0,
    )

    results = search_service.search_connections(request)

    assert results

    journey = next(result for result in results if result.legs[0].trip_id == "T2")

    assert journey.legs[0].departure_time == datetime(2026, 6, 2, 1, 30)
    assert journey.legs[0].arrival_time == datetime(2026, 6, 2, 1, 35)
