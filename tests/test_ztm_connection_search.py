from datetime import datetime

from services.ztm_connection_search import (
    ConnectionSearchRequest,
    ZTMConnectionSearchService,
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


def test_connection_with_two_transfers(multi_transfer_search_service):
    request = ConnectionSearchRequest(
        from_stop_id="S1",
        to_stop_id="S4",
        departure_time=datetime(2026, 6, 1, 5, 30),
        max_transfers=2,
    )

    results = multi_transfer_search_service.search_connections(request)

    assert len(results) == 1
    assert results[0].transfers == 2
    assert [leg.trip_id for leg in results[0].legs] == ["T1", "T3", "T4"]


def test_faster_transfer_beats_slower_direct_with_cutoff(multi_transfer_search_service):
    request = ConnectionSearchRequest(
        from_stop_id="S1",
        to_stop_id="S3",
        departure_time=datetime(2026, 6, 1, 5, 30),
        max_transfers=2,
    )

    results = multi_transfer_search_service.search_connections(request)

    # T1+T3 (06:20, 1 transfer) arrives earlier; T_SLOW (06:45, 0 transfers) has fewer transfers.
    # T_NO_DROP (06:30, 0 transfers) dominates T_SLOW, so T_SLOW is filtered out.
    assert len(results) == 2
    assert results[0].transfers == 1
    assert results[0].arrival_time == datetime(2026, 6, 1, 6, 20)
    assert results[1].transfers == 0
    assert results[1].arrival_time == datetime(2026, 6, 1, 6, 30)


def test_drop_off_prohibited_blocks_transfer_but_allows_riding_through(
    multi_transfer_search_service,
):
    # T_NO_DROP has drop_off_type=1 at S2, so it cannot be used to transfer to T3 at S2;
    # passenger must stay aboard T_NO_DROP until S3 (06:30).
    results = multi_transfer_search_service.search_connections(
        ConnectionSearchRequest(
            from_stop_id="S1",
            to_stop_id="S3",
            departure_time=datetime(2026, 6, 1, 5, 30),
            max_transfers=1,
        )
    )

    direct_no_drop = next(r for r in results if r.transfers == 0)
    assert [leg.trip_id for leg in direct_no_drop.legs] == ["T_NO_DROP"]
    assert direct_no_drop.arrival_time == datetime(2026, 6, 1, 6, 30)


def test_minimum_transfer_seconds_enforced(sample_schedule):
    # T1 arrives at S2 at 05:43, T3 departs S2 at 06:10 (27 min = 1620s gap)
    tight_ok = ZTMConnectionSearchService(schedule=sample_schedule, minimum_transfer_seconds=1620)
    too_tight = ZTMConnectionSearchService(schedule=sample_schedule, minimum_transfer_seconds=1621)

    request = ConnectionSearchRequest(
        from_stop_id="S1",
        to_stop_id="S3",
        departure_time=datetime(2026, 6, 1, 5, 30),
        max_transfers=1,
    )

    assert len(tight_ok.search_connections(request)) == 1
    assert too_tight.search_connections(request) == []


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
