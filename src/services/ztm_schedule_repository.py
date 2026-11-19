"""Date-based selection of independent, immutable-by-convention GTFS feeds."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from services.ztm_static_schedule import ZTMStaticSchedule

WARSAW = ZoneInfo("Europe/Warsaw")


def warsaw_today() -> date:
    return datetime.now(WARSAW).date()


@dataclass(frozen=True, slots=True)
class ScheduleEntry:
    filename: str
    modified_at: datetime
    schedule: ZTMStaticSchedule

    @property
    def date_range(self) -> tuple[date, date]:
        bounds = self.schedule.available_date_range
        if bounds is None or bounds[0] > bounds[1]:
            raise ValueError(f"GTFS archive {self.filename} has no valid date range")
        return bounds


class ScheduleRepository:
    """Own a collection of feeds, never a merged set of GTFS records.

    A single writer builds replacements before swapping the tuple reference.
    Readers retain their selected schedule even when a refresh removes it.
    """

    def __init__(self, entries: Iterable[ScheduleEntry] = ()) -> None:
        self._entries: tuple[ScheduleEntry, ...] = ()
        self.replace(entries)

    def list_schedules(self) -> tuple[ScheduleEntry, ...]:
        return self._entries

    def replace(self, entries: Iterable[ScheduleEntry]) -> None:
        replacement = tuple(entries)
        filenames: set[str] = set()
        for entry in replacement:
            if entry.filename in filenames:
                raise ValueError(f"Duplicate GTFS archive: {entry.filename}")
            filenames.add(entry.filename)
            _ = entry.date_range  # Validate before publishing any part of the collection.
            if entry.modified_at.utcoffset() is None:
                raise ValueError("Archive modification timestamps must include a timezone")
        self._entries = tuple(
            sorted(replacement, key=lambda entry: (entry.modified_at, entry.filename), reverse=True)
        )

    def for_date(self, day: date) -> ZTMStaticSchedule:
        entries = self._entries
        for entry in entries:
            start, end = entry.date_range
            if start <= day <= end:
                return entry.schedule

        ranges = sorted({entry.date_range for entry in entries})
        available = "; ".join(f"{start} to {end}" for start, end in ranges) or "none loaded"
        raise ValueError(
            f"No GTFS schedule covers {day}. Supported date ranges (inclusive): {available}."
        )
