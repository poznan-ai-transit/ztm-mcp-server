"""Complete, schedule-local platform groups and compatibility with existing indexes."""

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime

import pytest

from services.ztm_connection_search import ConnectionSearchRequest, ZTMConnectionSearchService
from services.ztm_schedule_repository import WARSAW, ScheduleEntry, ScheduleRepository
from services.ztm_static_schedule import ZTMStaticSchedule


def test_group_contains_all_platforms_even_without_trips_or_services(sample_gtfs):
    platform = sample_gtfs["stops"][0]
    rows = [
        {
            **platform,
            "stop_id": f"KAP{i:02d}",
            "stop_code": f"RKAP{i:02d}",
            "stop_name": " Rondo Kaponiera " if i == 0 else "Rondo Kaponiera",
            "zone_id": "B" if i == 0 else "A",
        }
        for i in range(11)
    ]
    schedule = ZTMStaticSchedule.load({"stops": rows})

    (group,) = schedule.get_all_stop_groups()
    assert group.stop_name == "Rondo Kaponiera"
    assert set(group.stop_ids) == {row["stop_id"] for row in rows}
    assert schedule.get_stop_group(group.group_id) is group
    platforms = schedule.get_stops_for_group(group.group_id)
    assert len(platforms) == 11
    for stop in platforms:
        assert stop is schedule.get_stop(stop.stop_id)
    assert platforms[0].stop_code == "RKAP00"
    assert platforms[0].zone_id == "B"
    assert platforms[0].stop_lat == float(platform["stop_lat"])
    assert platforms[0].stop_lon == float(platform["stop_lon"])

    # The current platform-based MCP search still has its own result limit.
    assert len(schedule.fuzzy_search_stops("Rondo Kaponiera")) == 10
    assert len(schedule.get_stops_for_group(group.group_id)) == 11


def test_single_platform_stops_are_groups_too(sample_schedule):
    groups = sample_schedule.get_all_stop_groups()
    assert {group.stop_name: group.stop_ids for group in groups} == {
        "Rynek": ("S1",),
        "Żabinko": ("S2",),
        "Dworzec": ("S3",),
    }


def test_exact_names_stay_separate_when_normalized_names_match(sample_gtfs):
    names = ("Żabinko", "Zabinko", "żabinko", "Żabinko Dworzec")
    rows = [
        {**sample_gtfs["stops"][0], "stop_id": str(i), "stop_name": name}
        for i, name in enumerate(names)
    ]
    schedule = ZTMStaticSchedule.load({"stops": rows})

    groups = schedule.get_all_stop_groups()
    assert len({group.group_id for group in groups}) == len(names)
    assert {group.stop_name: group.stop_ids for group in groups} == {
        name: (str(i),) for i, name in enumerate(names)
    }
    exact_matches = [
        match.stop.stop_name
        for match in schedule.fuzzy_search_stops("zabinko")
        if match.score == 1.0
    ]
    assert set(exact_matches) == set(names[:3])


def test_group_identity_and_membership_are_independent_of_input_order(sample_gtfs):
    rows = sample_gtfs["stops"]
    rows.append({**rows[0], "stop_id": "S4"})
    first = ZTMStaticSchedule.load({"stops": rows})
    reordered = ZTMStaticSchedule.load({"stops": list(reversed(rows))})

    assert first.data.stop_groups_by_id == reordered.data.stop_groups_by_id

    added = ZTMStaticSchedule.load(
        {"stops": [*rows, {**rows[0], "stop_id": "S5", "stop_name": "Another stop"}]}
    )
    for group in first.get_all_stop_groups():
        assert added.get_stop_group(group.group_id) == group


def test_group_loading_preserves_source_rows_and_membership_is_immutable(sample_gtfs):
    original = deepcopy(sample_gtfs)
    schedule = ZTMStaticSchedule.load(sample_gtfs)
    assert sample_gtfs == original

    group = schedule.get_all_stop_groups()[0]
    with pytest.raises(FrozenInstanceError):
        group.stop_ids = ("different-platform",)

    platforms = schedule.get_stops_for_group(group.group_id)
    platforms.clear()
    assert len(schedule.get_stops_for_group(group.group_id)) == len(group.stop_ids)


def test_matching_names_and_platform_ids_do_not_merge_schedules(sample_gtfs):
    template = sample_gtfs["stops"][0]
    first = ZTMStaticSchedule.load(
        {
            "stops": [
                {**template, "stop_id": "S1", "stop_name": "Rondo Kaponiera"},
                {**template, "stop_id": "S2", "stop_name": "Rondo Kaponiera"},
            ]
        }
    )
    second = ZTMStaticSchedule.load(
        {
            "stops": [
                {**template, "stop_id": "S1", "stop_name": "Rondo Kaponiera"},
                {**template, "stop_id": "S2", "stop_name": "Rynek"},
                {**template, "stop_id": "S3", "stop_name": "Rondo Kaponiera"},
            ]
        }
    )

    (first_group,) = first.get_all_stop_groups()
    second_group = second.get_stop_group(first_group.group_id)
    assert second_group is not first_group
    assert second_group.stop_ids == ("S1", "S3")
    assert first_group.stop_ids == ("S1", "S2")
    assert {stop.stop_id for stop in first.get_stops_for_group(first_group.group_id)} == {
        "S1",
        "S2",
    }
    assert {stop.stop_id for stop in second.get_stops_for_group(first_group.group_id)} == {
        "S1",
        "S3",
    }
    assert first.get_stop("S2").stop_name == "Rondo Kaponiera"
    assert second.get_stop("S2").stop_name == "Rynek"

    rynek_group = next(
        group for group in second.get_all_stop_groups() if group.stop_name == "Rynek"
    )
    assert first.get_stop_group(rynek_group.group_id) is None
    assert first.get_stops_for_group(rynek_group.group_id) == []


def test_refresh_updates_only_replaced_schedules_groups(sample_gtfs):
    sample_gtfs["stops"].append({**sample_gtfs["stops"][0], "stop_id": "S4"})
    june = ScheduleEntry(
        "june.zip",
        datetime(2026, 6, 1, tzinfo=WARSAW),
        ZTMStaticSchedule.load(sample_gtfs),
    )
    july_rows = deepcopy(sample_gtfs)
    july_rows["feed_info"][0].update(feed_start_date="20260701", feed_end_date="20260731")
    july = replace(june, filename="july.zip", schedule=ZTMStaticSchedule.load(july_rows))
    repository = ScheduleRepository((june, july))
    original_june = repository.for_date(date(2026, 6, 15))
    original_july_groups = july.schedule.get_all_stop_groups()
    rynek_group = next(
        group for group in original_june.get_all_stop_groups() if group.stop_name == "Rynek"
    )
    removed_group = next(
        group for group in original_june.get_all_stop_groups() if group.stop_name == "Dworzec"
    )

    refreshed_rows = deepcopy(sample_gtfs)
    refreshed_rows["stops"] = [
        stop for stop in refreshed_rows["stops"] if stop["stop_id"] not in {"S3", "S4"}
    ]
    refreshed_rows["stops"].append({**sample_gtfs["stops"][0], "stop_id": "S5"})
    refreshed_june = ZTMStaticSchedule.load(refreshed_rows)
    repository.replace((replace(june, schedule=refreshed_june), july))

    assert repository.for_date(date(2026, 6, 15)) is refreshed_june
    assert refreshed_june.get_stop_group(rynek_group.group_id).stop_ids == ("S1", "S5")
    assert refreshed_june.get_stop_group(removed_group.group_id) is None
    assert original_june.get_stop_group(rynek_group.group_id).stop_ids == ("S1", "S4")
    assert original_june.get_stop_group(removed_group.group_id) is removed_group
    assert repository.for_date(date(2026, 7, 15)) is july.schedule
    for group in original_july_groups:
        assert july.schedule.get_stop_group(group.group_id) is group


def test_group_membership_does_not_change_connection_or_departure_indexes(sample_gtfs):
    original = ZTMStaticSchedule.load(sample_gtfs)
    sample_gtfs["stops"].append({**sample_gtfs["stops"][0], "stop_id": "S4"})
    with_extra_platform = ZTMStaticSchedule.load(sample_gtfs)

    assert (
        original.data.connections_by_service_day
        == with_extra_platform.data.connections_by_service_day
    )
    assert (
        original.data.connection_index_lookback_days
        == with_extra_platform.data.connection_index_lookback_days
    )
    assert original.data.departures_by_stop == with_extra_platform.data.departures_by_stop
    assert original.data.routes_by_stop == with_extra_platform.data.routes_by_stop
    assert original.data.stops_by_route == with_extra_platform.data.stops_by_route

    request = ConnectionSearchRequest("S1", "S3", datetime(2026, 6, 1, 5, 30))
    assert ZTMConnectionSearchService(original).search_connections(request) == (
        ZTMConnectionSearchService(with_extra_platform).search_connections(request)
    )
