"""Tests for the in-memory static-GTFS schedule store (ZTMStaticSchedule)."""

from __future__ import annotations

import threading

from services.ztm_static_schedule import ZTMStaticSchedule

# --------------------------------------------------------------------------- #
# Singleton behaviour
# --------------------------------------------------------------------------- #


def test_instance_returns_same_object():
    assert ZTMStaticSchedule.instance() is ZTMStaticSchedule.instance()


def test_constructor_and_instance_share_singleton():
    assert ZTMStaticSchedule() is ZTMStaticSchedule.instance()


def test_init_runs_only_once():
    """A second construction must not wipe data already stored on the singleton."""
    schedule = ZTMStaticSchedule.instance()
    schedule.set_static_gtfs({"stops": [{"stop_id": "S1"}]})
    again = ZTMStaticSchedule()
    assert again.get_stops() == [{"stop_id": "S1"}]


# --------------------------------------------------------------------------- #
# set / get_static_gtfs
# --------------------------------------------------------------------------- #


def test_get_static_gtfs_returns_stored_payload(sample_gtfs):
    schedule = ZTMStaticSchedule.instance()
    schedule.set_static_gtfs(sample_gtfs)
    assert schedule.get_static_gtfs() is sample_gtfs


def test_set_static_gtfs_replaces_previous_payload():
    schedule = ZTMStaticSchedule.instance()
    schedule.set_static_gtfs({"stops": [{"stop_id": "OLD"}]})
    schedule.set_static_gtfs({"stops": [{"stop_id": "NEW"}]})
    assert schedule.get_stops() == [{"stop_id": "NEW"}]


# --------------------------------------------------------------------------- #
# Getters with no data loaded
# --------------------------------------------------------------------------- #


def test_empty_collection_getters_return_empty_lists():
    schedule = ZTMStaticSchedule.instance()
    assert schedule.get_stops() == []
    assert schedule.get_routes() == []
    assert schedule.get_stop_times() == []
    assert schedule.get_static_gtfs() == {}


def test_empty_lookup_getters_return_none_or_empty():
    schedule = ZTMStaticSchedule.instance()
    assert schedule.get_stop_by_id("nope") is None
    assert schedule.get_route_by_id("nope") is None
    assert schedule.get_stop_times_for_trip("nope") == []
    assert schedule.get_stop_times_for_stop("nope") == []


# --------------------------------------------------------------------------- #
# Getters and index lookups with data loaded
# --------------------------------------------------------------------------- #


def _loaded(sample_gtfs) -> ZTMStaticSchedule:
    schedule = ZTMStaticSchedule.instance()
    schedule.set_static_gtfs(sample_gtfs)
    return schedule


def test_collection_getters_return_all_rows(sample_gtfs):
    schedule = _loaded(sample_gtfs)
    assert len(schedule.get_stops()) == 2
    assert len(schedule.get_routes()) == 2
    assert len(schedule.get_stop_times()) == 3


def test_get_stop_by_id(sample_gtfs):
    schedule = _loaded(sample_gtfs)
    assert schedule.get_stop_by_id("S2")["stop_name"] == "Żabinko"


def test_get_route_by_id(sample_gtfs):
    schedule = _loaded(sample_gtfs)
    assert schedule.get_route_by_id("R1")["route_long_name"] == "Łęczyca"


def test_get_stop_times_for_trip(sample_gtfs):
    schedule = _loaded(sample_gtfs)
    times = schedule.get_stop_times_for_trip("T1")
    assert [t["stop_id"] for t in times] == ["S1", "S2"]


def test_get_stop_times_for_stop(sample_gtfs):
    schedule = _loaded(sample_gtfs)
    # S1 is visited by both trips, S2 by one.
    assert len(schedule.get_stop_times_for_stop("S1")) == 2
    assert len(schedule.get_stop_times_for_stop("S2")) == 1


# --------------------------------------------------------------------------- #
# Concurrency / locking
# --------------------------------------------------------------------------- #


def test_read_lock_is_reentrant():
    """read_lock wraps an RLock, so nesting from the same thread must not deadlock."""
    schedule = ZTMStaticSchedule.instance()
    with schedule.read_lock(), schedule.read_lock():
        assert schedule.get_static_gtfs() == {}


def test_concurrent_writers_leave_consistent_state():
    schedule = ZTMStaticSchedule.instance()

    def writer(value):
        for _ in range(50):
            schedule.set_static_gtfs({"stops": [{"stop_id": value}]})

    threads = [threading.Thread(target=writer, args=(v,)) for v in ("A", "B", "C")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Whichever writer finished last, the snapshot is one coherent value.
    assert schedule.get_stops()[0]["stop_id"] in {"A", "B", "C"}
