"""Selection and publication of independent GTFS feeds."""

from copy import deepcopy
from dataclasses import replace
from datetime import date, datetime

import pytest

from services.ztm_schedule_repository import WARSAW, ScheduleEntry, ScheduleRepository
from services.ztm_static_schedule import ZTMStaticSchedule


@pytest.fixture
def entries(sample_gtfs):
    june = ZTMStaticSchedule.load(sample_gtfs)
    july_gtfs = deepcopy(sample_gtfs)
    july_gtfs["feed_info"][0].update(feed_start_date="20260701", feed_end_date="20260731")
    july = ZTMStaticSchedule.load(july_gtfs)
    return (
        ScheduleEntry("june.zip", datetime(2026, 6, 1, tzinfo=WARSAW), june),
        ScheduleEntry("july.zip", datetime(2026, 6, 2, tzinfo=WARSAW), july),
    )


def test_repository_selects_independent_feeds_at_inclusive_boundaries(entries):
    june, july = entries
    repository = ScheduleRepository(entries)
    for day in (date(2026, 6, 1), date(2026, 6, 30)):
        assert repository.for_date(day) is june.schedule
    for day in (date(2026, 7, 1), date(2026, 7, 31)):
        assert repository.for_date(day) is july.schedule


@pytest.mark.parametrize("day", [date(2026, 5, 31), date(2026, 8, 1)])
def test_repository_reports_available_ranges_for_uncovered_dates(entries, day):
    with pytest.raises(ValueError, match=f"No GTFS schedule covers {day}") as exc:
        ScheduleRepository(entries).for_date(day)
    assert "2026-06-01 to 2026-06-30" in str(exc.value)
    assert "2026-07-01 to 2026-07-31" in str(exc.value)


def test_repository_rejects_gap_between_feeds(entries):
    june, july = entries
    june.schedule.data.feed_info = replace(june.schedule.feed_info, end_date=date(2026, 6, 29))
    repository = ScheduleRepository((june, july))
    with pytest.raises(ValueError, match="No GTFS schedule covers 2026-06-30"):
        repository.for_date(date(2026, 6, 30))


def test_repository_prefers_latest_modified_feed_in_overlap(entries, sample_gtfs):
    june, _ = entries
    newer_schedule = ZTMStaticSchedule.load(sample_gtfs)
    newer = replace(
        june,
        filename="newer.zip",
        modified_at=datetime(2026, 6, 3, tzinfo=WARSAW),
        schedule=newer_schedule,
    )
    for order in ((june, newer), (newer, june)):
        assert ScheduleRepository(order).for_date(date(2026, 6, 15)) is newer_schedule


def test_repository_breaks_modification_ties_by_filename(entries, sample_gtfs):
    june, _ = entries
    other = replace(june, filename="z.zip", schedule=ZTMStaticSchedule.load(sample_gtfs))
    assert ScheduleRepository((other, june)).for_date(date(2026, 6, 15)) is other.schedule


def test_replace_preserves_previously_selected_feed_and_snapshot(entries):
    june, july = entries
    repository = ScheduleRepository((june,))
    snapshot = repository.list_schedules()
    selected = repository.for_date(date(2026, 6, 15))

    repository.replace((july,))

    assert repository.list_schedules() == (july,)
    assert snapshot == (june,)
    assert selected.get_stop("S1").stop_name == "Rynek"
    with pytest.raises(ValueError, match="No GTFS schedule covers"):
        repository.for_date(date(2026, 6, 15))


@pytest.mark.parametrize("invalid", ["duplicate", "no_dates", "reversed_dates", "naive_timestamp"])
def test_invalid_replacement_leaves_published_entries_untouched(entries, invalid):
    june, july = entries
    repository = ScheduleRepository((june,))
    previous = repository.list_schedules()
    if invalid == "duplicate":
        replacement = (july, july)
    elif invalid == "no_dates":
        replacement = (replace(july, schedule=ZTMStaticSchedule()),)
    elif invalid == "reversed_dates":
        july.schedule.data.feed_info = replace(july.schedule.feed_info, end_date=date(2026, 6, 30))
        replacement = (july,)
    else:
        replacement = (replace(july, modified_at=datetime(2026, 6, 2)),)

    with pytest.raises(ValueError):
        repository.replace(replacement)
    assert repository.list_schedules() is previous


def test_empty_repository_reports_none_loaded():
    with pytest.raises(ValueError, match="none loaded"):
        ScheduleRepository().for_date(date(2026, 6, 15))
