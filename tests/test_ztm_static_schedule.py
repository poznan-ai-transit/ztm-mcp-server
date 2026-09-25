"""Tests for the object-based static-GTFS schedule store (ZTMStaticSchedule)."""

from __future__ import annotations

import threading
from datetime import date

from services.ztm_static_schedule import ZTMStaticSchedule


def test_instance_returns_same_object():
    assert ZTMStaticSchedule.instance() is ZTMStaticSchedule.instance()


def test_constructor_and_instance_share_singleton():
    assert ZTMStaticSchedule() is ZTMStaticSchedule.instance()


def _loaded(sample_gtfs) -> ZTMStaticSchedule:
    return ZTMStaticSchedule.load(sample_gtfs)


def test_load_returns_singleton_and_builds_indexes(sample_gtfs):
    schedule = _loaded(sample_gtfs)
    assert schedule is ZTMStaticSchedule.instance()
    assert [stop.stop_id for stop in schedule.get_all_stops()] == ["S1", "S2", "S3"]
    assert [route.route_id for route in schedule.get_all_routes()] == ["R1", "R2"]
    assert schedule.feed_info is not None
    assert schedule.feed_info.publisher_name == "ZTM Poznań"


def test_load_keeps_singleton_identity_while_replacing_data(sample_gtfs):
    schedule = ZTMStaticSchedule.load(sample_gtfs)
    same_instance = ZTMStaticSchedule.instance()

    alt_gtfs = {
        **sample_gtfs,
        "stops": [
            {**sample_gtfs["stops"][0], "stop_id": "X1", "stop_name": "Alt Stop 1"},
            {**sample_gtfs["stops"][1], "stop_id": "X2", "stop_name": "Alt Stop 2"},
        ],
        "routes": [
            {**sample_gtfs["routes"][0], "route_id": "RX", "route_long_name": "Alt Route"},
        ],
        "trips": [
            {**sample_gtfs["trips"][0], "trip_id": "TX", "route_id": "RX"},
        ],
        "stop_times": [
            {**sample_gtfs["stop_times"][0], "trip_id": "TX", "stop_id": "X1"},
            {**sample_gtfs["stop_times"][1], "trip_id": "TX", "stop_id": "X2", "stop_sequence": "1"},
        ],
    }

    reloaded = ZTMStaticSchedule.load(alt_gtfs)

    assert schedule is same_instance is reloaded
    assert [stop.stop_id for stop in reloaded.get_all_stops()] == ["X1", "X2"]
    assert [route.route_id for route in reloaded.get_all_routes()] == ["RX"]
    assert [time.stop_id for time in reloaded.get_stop_times_for_trip("TX")] == ["X1", "X2"]


def test_concurrent_loads_leave_consistent_state(sample_gtfs):
    schedule = ZTMStaticSchedule.instance()
    feed_a = sample_gtfs
    feed_b = {
        **sample_gtfs,
        "stops": [
            {**sample_gtfs["stops"][0], "stop_id": "Y1", "stop_name": "Feed B 1"},
            {**sample_gtfs["stops"][1], "stop_id": "Y2", "stop_name": "Feed B 2"},
        ],
        "routes": [
            {**sample_gtfs["routes"][0], "route_id": "RB", "route_long_name": "Feed B Route"},
        ],
        "trips": [
            {**sample_gtfs["trips"][0], "trip_id": "TB", "route_id": "RB"},
        ],
        "stop_times": [
            {**sample_gtfs["stop_times"][0], "trip_id": "TB", "stop_id": "Y1"},
            {**sample_gtfs["stop_times"][1], "trip_id": "TB", "stop_id": "Y2", "stop_sequence": "1"},
        ],
    }

    start = threading.Barrier(3)

    def loader(feed: dict) -> None:
        start.wait()
        for _ in range(20):
            ZTMStaticSchedule.load(feed)

    threads = [threading.Thread(target=loader, args=(feed,)) for feed in (feed_a, feed_b)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join()

    final_stop_ids = {stop.stop_id for stop in schedule.get_all_stops()}
    final_route_ids = {route.route_id for route in schedule.get_all_routes()}

    assert (final_stop_ids, final_route_ids) in [
        ({"S1", "S2", "S3"}, {"R1", "R2"}),
        ({"Y1", "Y2"}, {"RB"}),
    ]


def test_empty_collection_getters_return_empty_lists():
    schedule = ZTMStaticSchedule.instance()
    assert schedule.get_all_stops() == []
    assert schedule.get_all_routes() == []
    assert schedule.get_stop("nope") is None
    assert schedule.get_route("nope") is None
    assert schedule.get_stop_times_for_trip("nope") == []
    assert schedule.get_routes_for_stop("nope") == []
    assert schedule.get_stops_by_name("nope") == []
    assert schedule.get_route_summary("nope") is None


def test_get_stop_and_route_by_id(sample_gtfs):
    schedule = _loaded(sample_gtfs)
    assert schedule.get_stop("S2").stop_name == "Żabinko"
    assert schedule.get_route("R1").route_long_name == "Łęczyca"


def test_get_stop_times_for_trip(sample_gtfs):
    schedule = _loaded(sample_gtfs)
    times = schedule.get_stop_times_for_trip("T1")
    assert [time.stop_id for time in times] == ["S1", "S2"]


def test_get_routes_for_stop(sample_gtfs):
    schedule = _loaded(sample_gtfs)
    assert {route.route_id for route in schedule.get_routes_for_stop("S2")} == {"R1", "R2"}


def test_get_stop_sequence_for_route(sample_gtfs):
    schedule = _loaded(sample_gtfs)
    assert [stop.stop_name for stop in schedule.get_stop_sequence_for_route("R1", 0)] == [
        "Rynek",
        "Żabinko",
    ]


def test_get_stops_by_name_is_normalized(sample_gtfs):
    schedule = _loaded(sample_gtfs)
    assert [stop.stop_id for stop in schedule.get_stops_by_name("rynek")] == ["S1"]


def test_fuzzy_search_stops_handles_partial_match(sample_gtfs):
    schedule = _loaded(sample_gtfs)
    hits = schedule.fuzzy_search_stops("zabink")
    assert hits and hits[0].stop.stop_id == "S2"


def test_get_active_services(sample_gtfs):
    schedule = _loaded(sample_gtfs)
    assert schedule.get_active_services(date(2026, 6, 15)) == {"WKD"}


def test_get_next_departures(sample_gtfs):
    schedule = _loaded(sample_gtfs)
    departures = schedule.get_next_departures("S1", after_secs=5 * 3600, day=date(2026, 6, 15), limit=2)
    assert [entry["departure_time"] for entry in departures] == ["05:38:00", "25:30:00"]
    assert departures[0]["route_id"] == "R1"


def test_get_trip_summary_includes_datetimes(sample_gtfs):
    schedule = _loaded(sample_gtfs)
    summary = schedule.get_trip_summary("T2", service_day=date(2026, 6, 15))
    assert summary[0]["departure_datetime"] == "2026-06-16T01:30:00"


def test_get_route_summary(sample_gtfs):
    schedule = _loaded(sample_gtfs)
    summary = schedule.get_route_summary("R1")
    assert summary["route_long_name"] == "Łęczyca"
    assert summary["directions"][0] == ["Rynek", "Żabinko"]


def test_real_feed_loads_into_object_model(real_gtfs):
    schedule = ZTMStaticSchedule.load(real_gtfs)
    assert len(schedule.get_all_stops()) == len(real_gtfs["stops"])
    assert len(schedule.get_all_routes()) == len(real_gtfs["routes"])
