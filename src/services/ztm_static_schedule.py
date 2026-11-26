"""
In-memory storage for ZTM Poznań GTFS data.

Design notes
------------
- All raw GTFS rows are parsed once into small, slotted dataclasses
  (Stop, Route, Trip, StopTime, Service, FeedInfo).
- On top of those records we build a handful of index dicts/lists that
  directly answer the queries an MCP tool will need:
    * _stops_by_id          - exact stop lookup
    * _stop_groups_by_id    - stable group ID -> all platforms with the same name
    * _stop_group_ids_by_normalized_name - normalized name -> [group_id, ...]
    * _trips_by_id          - exact trip lookup
    * _stop_times_by_trip   - trip_id -> [StopTime, ...] sorted by stop_sequence
    * _departures_by_stop   - stop_id -> [(departure_secs, trip_id, stop_sequence), ...]
                               sorted by departure_secs (binary-searchable with bisect)
    * _services             - service_id -> Service (weekly pattern + exceptions)
    * _routes_by_stop       - stop_id -> {route_id, ...}
    * _stops_by_route       - route_id -> {direction_id -> [stop_id, ...]} (modal pattern)
- Each schedule owns a separate PrecomputedData object. Loading a feed creates
  a new schedule; published schedules are immutable by convention.

On timestamps
-------------
GTFS times are *not* wall-clock times: `arrival_time`/`departure_time` can
exceed 24:00:00 (e.g. "25:30:00") to represent a trip that runs past
midnight while still belonging to the previous service day. A stop_time
also isn't tied to any single calendar date by itself -- it's tied to a
*service pattern* (via the trip's service_id) that can be active on many
different dates.

Because of that, we deliberately do NOT store stop_times as real
datetimes. We keep `arrival_secs` / `departure_secs` (seconds since the
start of the service day) as the canonical, storage-level representation,
since that's what's actually correct for GTFS and is what makes
`next_departures` sortable/bisectable.

Instead, `StopTime.to_datetime(service_day)` converts a stored offset into
a real `datetime` *on demand*, anchored to a concrete calendar date chosen
by the caller (e.g. "today" or whatever date the MCP tool is answering
for). This is the point where "seconds since midnight" becomes a "normal
timestamp" -- it has to happen with a date in hand, not at parse time.
"""

from __future__ import annotations

import unicodedata
from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from hashlib import sha256

from logging_config import logger

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def hms_to_secs(value: str) -> int:
    """Convert 'HH:MM:SS' (possibly >24h, e.g. '25:30:00') to seconds since
    the start of the GTFS service day."""

    # Check if string has 3 parts separated by colons
    parts = value.strip().split(":")
    if len(parts) != 3:
        raise ValueError(f"Invalid GTFS time format: {value!r}; expected HH:MM:SS")

    # Check if values are only digits
    hour_text, minute_text, second_text = parts
    if not hour_text.isdigit() or not minute_text.isdigit() or not second_text.isdigit():
        raise ValueError(f"Invalid GTFS time value: {value!r}; expected numeric HH:MM:SS")

    hour = int(hour_text)
    minute = int(minute_text)
    second = int(second_text)
    # Validate ranges for minute and second
    if not (0 <= minute < 60 and 0 <= second < 60):
        raise ValueError(f"Invalid GTFS time value: {value!r}; minute/second out of range")

    return hour * 3600 + minute * 60 + second


def secs_to_hms(secs: int) -> str:
    """Inverse of hms_to_secs, for display (keeps >24h notation)."""
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def secs_to_datetime(secs: int, service_day: date) -> datetime:
    """Convert GTFS seconds-since-service-day-start to a real wall-clock
    datetime anchored to `service_day`.  Works correctly for overnight trips
    where secs >= 86400."""
    return datetime(service_day.year, service_day.month, service_day.day) + timedelta(seconds=secs)


def normalize_name(name: str) -> str:
    """Lowercase, strip whitespace/diacritics for exact/fuzzy stop-name matching."""
    name = name.strip().lower()
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    return name


def _parse_date(value: str) -> date:
    value = value.strip()
    # Check if date has correct format (YYYYMMDD)
    if len(value) != 8 or not value.isdigit():
        raise ValueError(f"Invalid GTFS date format: {value!r}; expected YYYYMMDD")

    try:
        return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))
    except ValueError as exc:
        raise ValueError(f"Invalid GTFS date value: {value!r}") from exc


# ---------------------------------------------------------------------------
# Core records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Stop:
    stop_id: str
    stop_code: str
    stop_name: str
    stop_lat: float
    stop_lon: float
    zone_id: str


@dataclass(frozen=True, slots=True)
class StopGroup:
    """All platforms with one exact name in a single schedule.

    IDs remain stable across loads and membership changes. Always resolve an
    ID in the date-selected schedule; matching IDs do not share platform data.
    """

    group_id: str
    stop_name: str
    stop_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Route:
    route_id: str
    agency_id: str
    route_short_name: str
    route_long_name: str
    route_type: int


@dataclass(frozen=True, slots=True)
class Trip:
    trip_id: str
    route_id: str
    service_id: str
    trip_headsign: str
    direction_id: int


@dataclass(frozen=True, slots=True)
class StopTime:
    trip_id: str
    stop_id: str
    arrival_secs: int
    departure_secs: int
    stop_sequence: int
    pickup_type: int
    drop_off_type: int


@dataclass(frozen=True, slots=True)
class Service:
    service_id: str
    weekday_pattern: tuple[bool, bool, bool, bool, bool, bool, bool]  # Mon..Sun
    start_date: date
    end_date: date
    added_dates: frozenset[date] = field(default_factory=frozenset)
    removed_dates: frozenset[date] = field(default_factory=frozenset)

    def runs_on(self, day: date) -> bool:
        if day in self.removed_dates:
            return False
        if day in self.added_dates:
            return True
        if not (self.start_date <= day <= self.end_date):
            return False
        return self.weekday_pattern[day.weekday()]


@dataclass(frozen=True, slots=True)
class FeedInfo:
    publisher_name: str
    publisher_url: str
    lang: str
    start_date: date
    end_date: date


@dataclass(frozen=True, slots=True)
class StopMatch:
    """A fuzzy/substring search hit, with a score so callers (and the LLM)
    can tell a confident match from a guess."""

    stop: Stop
    score: float  # 1.0 = exact normalized match, lower = weaker match


@dataclass(frozen=True, slots=True)
class ServiceConnectionTemplate:
    """Date-independent consecutive-stop connection template using integer
    seconds since service-day midnight so calendar days sharing the exact
    same set of active services reuse a single sorted tuple."""

    trip_id: str
    route_id: str
    departure_stop_id: str
    arrival_stop_id: str
    departure_secs: int
    arrival_secs: int
    departure_sequence: int
    pickup_type: int
    drop_off_type: int


@dataclass(slots=True)
class PrecomputedData:
    feed_info: FeedInfo | None = None
    stops_by_id: dict[str, Stop] = field(default_factory=dict)
    stop_groups_by_id: dict[str, StopGroup] = field(default_factory=dict)
    stop_group_ids_by_normalized_name: dict[str, list[str]] = field(default_factory=dict)
    routes_by_id: dict[str, Route] = field(default_factory=dict)
    trips_by_id: dict[str, Trip] = field(default_factory=dict)
    services: dict[str, Service] = field(default_factory=dict)
    stop_times_by_trip: dict[str, list[StopTime]] = field(default_factory=dict)
    departures_by_stop: dict[str, list[tuple[int, str, int]]] = field(default_factory=dict)
    routes_by_stop: dict[str, set[str]] = field(default_factory=dict)
    stops_by_route: dict[str, dict[int, list[str]]] = field(default_factory=dict)
    connections_by_service_day: dict[date, tuple[ServiceConnectionTemplate, ...]] = field(
        default_factory=dict
    )
    connection_index_lookback_days: int = 0


# ---------------------------------------------------------------------------
# Container with indexes
# ---------------------------------------------------------------------------


class ZTMStaticSchedule:
    def __init__(self) -> None:
        self.data = PrecomputedData()

    # -- properties for top-level metadata -----------------------------------

    @property
    def feed_info(self) -> FeedInfo | None:
        """Feed publisher metadata, or None if feed_info.txt was absent."""
        return self.data.feed_info

    @staticmethod
    def _date_range_for(data: PrecomputedData) -> tuple[date, date] | None:
        if data.feed_info is not None:
            return data.feed_info.start_date, data.feed_info.end_date

        dates: list[date] = []
        for service in data.services.values():
            if (service.start_date, service.end_date) != (date.min, date.max):
                dates.extend((service.start_date, service.end_date))
            dates.extend(service.added_dates)
        return (min(dates), max(dates)) if dates else None

    @property
    def available_date_range(self) -> tuple[date, date] | None:
        """Published feed bounds, falling back to service calendars and added dates."""
        return self._date_range_for(self.data)

    def validate_date_range(self, start_date: date, end_date: date | None = None) -> None:
        """Require a single date or an ordered, inclusive range within the loaded feed."""
        available_range = self.available_date_range
        if available_range is None:
            raise ValueError(
                "The GTFS dataset has no available date range. Load schedule data first."
            )

        available_start, available_end = available_range
        end_date = end_date or start_date
        if not available_start <= start_date <= end_date <= available_end:
            raise ValueError(
                f"Requested date range {start_date} to {end_date} is not supported. "
                f"Supported date range: {available_start} to {available_end} (inclusive)."
            )

    # -- loading -----------------------------------------------------------

    @classmethod
    def load(cls, rows_by_file: dict[str, list[dict[str, str]]]) -> ZTMStaticSchedule:
        storage = cls()
        data = PrecomputedData()

        data.feed_info = storage._load_feed_info(rows_by_file.get("feed_info", []))
        storage._load_stops(data, rows_by_file.get("stops", []))
        storage._load_routes(data, rows_by_file.get("routes", []))
        storage._load_trips(data, rows_by_file.get("trips", []))
        storage._load_calendar(data, rows_by_file.get("calendar", []))
        storage._load_calendar_dates(data, rows_by_file.get("calendar_dates", []))
        storage._load_stop_times(data, rows_by_file.get("stop_times", []))

        storage._build_departure_index(data)
        storage._build_route_stop_indexes(data)
        storage._build_connection_index(data)

        storage.data = data
        logger.debug("GTFS data loaded into schedule storage")
        return storage

    def _load_feed_info(self, rows: list[dict[str, str]]) -> FeedInfo | None:
        if not rows:
            return None
        row = rows[0]
        return FeedInfo(
            publisher_name=row["feed_publisher_name"],
            publisher_url=row["feed_publisher_url"],
            lang=row["feed_lang"],
            start_date=_parse_date(row["feed_start_date"]),
            end_date=_parse_date(row["feed_end_date"]),
        )

    def _load_stops(self, data: PrecomputedData, rows: list[dict[str, str]]) -> None:
        for row in rows:
            stop = Stop(
                stop_id=row["stop_id"].strip(),
                stop_code=row["stop_code"].strip(),
                stop_name=row["stop_name"].strip(),
                stop_lat=float(row["stop_lat"]),
                stop_lon=float(row["stop_lon"]),
                zone_id=row["zone_id"].strip(),
            )
            data.stops_by_id[stop.stop_id] = stop

        stop_ids_by_name: dict[str, list[str]] = {}
        for stop in data.stops_by_id.values():
            stop_ids_by_name.setdefault(stop.stop_name, []).append(stop.stop_id)

        for name, stop_ids in stop_ids_by_name.items():
            group_id = "stop-group:" + sha256(name.encode("utf-8")).hexdigest()
            data.stop_groups_by_id[group_id] = StopGroup(
                group_id=group_id,
                stop_name=name,
                stop_ids=tuple(sorted(stop_ids)),
            )
            key = normalize_name(name)
            data.stop_group_ids_by_normalized_name.setdefault(key, []).append(group_id)

    def _load_routes(self, data: PrecomputedData, rows: list[dict[str, str]]) -> None:
        for row in rows:
            route = Route(
                route_id=row["route_id"].strip(),
                agency_id=row["agency_id"].strip(),
                route_short_name=row["route_short_name"].strip(),
                route_long_name=row["route_long_name"].strip(),
                route_type=int(row["route_type"]),
            )
            data.routes_by_id[route.route_id] = route

    def _load_trips(self, data: PrecomputedData, rows: list[dict[str, str]]) -> None:
        for row in rows:
            trip = Trip(
                trip_id=row["trip_id"].strip(),
                route_id=row["route_id"].strip(),
                service_id=row["service_id"].strip(),
                trip_headsign=row["trip_headsign"].strip(),
                direction_id=int(row["direction_id"]),
            )
            data.trips_by_id[trip.trip_id] = trip

    def _load_calendar(self, data: PrecomputedData, rows: list[dict[str, str]]) -> None:
        if not rows:
            return
        for row in rows:
            service_id = row["service_id"].strip()
            pattern = tuple(
                row[day] == "1"
                for day in (
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "saturday",
                    "sunday",
                )
            )
            data.services[service_id] = Service(
                service_id=service_id,
                weekday_pattern=pattern,  # type: ignore[arg-type]
                start_date=_parse_date(row["start_date"]),
                end_date=_parse_date(row["end_date"]),
            )

    def _load_calendar_dates(self, data: PrecomputedData, rows: list[dict[str, str]]) -> None:
        if not rows:
            return
        added: dict[str, set[date]] = {}
        removed: dict[str, set[date]] = {}
        for row in rows:
            service_id = row["service_id"].strip()
            day = _parse_date(row["date"])
            if row["exception_type"].strip() == "1":
                added.setdefault(service_id, set()).add(day)
            else:
                removed.setdefault(service_id, set()).add(day)

        for service_id in set(added) | set(removed):
            extra_added = frozenset(added.get(service_id, set()))
            extra_removed = frozenset(removed.get(service_id, set()))
            existing = data.services.get(service_id)
            if existing is None:
                # service_id only defined via calendar_dates (no weekly pattern)
                data.services[service_id] = Service(
                    service_id=service_id,
                    weekday_pattern=(False,) * 7,
                    start_date=date.min,
                    end_date=date.max,
                    added_dates=extra_added,
                    removed_dates=extra_removed,
                )
            else:
                data.services[service_id] = Service(
                    service_id=existing.service_id,
                    weekday_pattern=existing.weekday_pattern,
                    start_date=existing.start_date,
                    end_date=existing.end_date,
                    added_dates=extra_added,
                    removed_dates=extra_removed,
                )

    def _load_stop_times(self, data: PrecomputedData, rows: list[dict[str, str]]) -> None:
        for row in rows:
            st = StopTime(
                trip_id=row["trip_id"].strip(),
                stop_id=row["stop_id"].strip(),
                arrival_secs=hms_to_secs(row["arrival_time"]),
                departure_secs=hms_to_secs(row["departure_time"]),
                stop_sequence=int(row["stop_sequence"]),
                pickup_type=int(row["pickup_type"]),
                drop_off_type=int(row["drop_off_type"]),
            )
            data.stop_times_by_trip.setdefault(st.trip_id, []).append(st)

        for stop_times in data.stop_times_by_trip.values():
            stop_times.sort(key=lambda st: st.stop_sequence)

    def _build_departure_index(self, data: PrecomputedData) -> None:
        for trip_id, stop_times in data.stop_times_by_trip.items():
            for st in stop_times:
                data.departures_by_stop.setdefault(st.stop_id, []).append(
                    (st.departure_secs, trip_id, st.stop_sequence)
                )

        for entries in data.departures_by_stop.values():
            entries.sort(key=lambda e: e[0])

    def _build_route_stop_indexes(self, data: PrecomputedData) -> None:
        pattern_counts: dict[tuple[str, int], Counter[tuple[str, ...]]] = {}
        for trip_id, stop_times in data.stop_times_by_trip.items():
            trip = data.trips_by_id.get(trip_id)
            if trip is None:
                continue

            route_id = trip.route_id
            direction_id = trip.direction_id

            stop_ids = tuple(st.stop_id for st in stop_times)

            # _routes_by_stop
            for stop_id in stop_ids:
                data.routes_by_stop.setdefault(stop_id, set()).add(route_id)

            # count route patterns
            pattern_counts.setdefault((route_id, direction_id), Counter())[stop_ids] += 1

        # select most common pattern per route+direction
        for (route_id, direction_id), counter in pattern_counts.items():
            most_common_pattern, _ = counter.most_common(1)[0]

            data.stops_by_route.setdefault(route_id, {})[direction_id] = list(most_common_pattern)

    def _build_connection_index(self, data: PrecomputedData) -> None:
        """
        Build sorted connection templates deduplicated by active service set.

        Each unique combination of active service_ids in the feed is built
        and sorted once as integer-offset templates, then shared across all
        calendar dates that run that exact service set.
        """
        max_departure_secs = max(
            (
                stop_time.departure_secs
                for stop_times in data.stop_times_by_trip.values()
                for stop_time in stop_times[:-1]
            ),
            default=0,
        )

        lookback_days = max_departure_secs // 86400
        data.connection_index_lookback_days = lookback_days

        # Determine the date range using the same rules as available_date_range.
        if data.feed_info is not None:
            start_date = data.feed_info.start_date
            end_date = data.feed_info.end_date
        else:
            date_range = self._date_range_for(data)
            if date_range is None:
                logger.debug("No service dates; connection index is empty")
                return
            start_date, end_date = date_range

        try:
            day = start_date - timedelta(days=lookback_days)
        except OverflowError:
            day = date.min

        # Pre-build date-independent connection templates grouped by service_id once.
        templates_by_service: dict[str, list[ServiceConnectionTemplate]] = {}

        for trip_id, stop_times in data.stop_times_by_trip.items():
            trip = data.trips_by_id.get(trip_id)
            if trip is None or len(stop_times) < 2:
                continue

            service_templates = templates_by_service.setdefault(trip.service_id, [])
            for departure, arrival in zip(stop_times, stop_times[1:], strict=False):
                service_templates.append(
                    ServiceConnectionTemplate(
                        trip_id=trip.trip_id,
                        route_id=trip.route_id,
                        departure_stop_id=departure.stop_id,
                        arrival_stop_id=arrival.stop_id,
                        departure_secs=departure.departure_secs,
                        arrival_secs=arrival.arrival_secs,
                        departure_sequence=departure.stop_sequence,
                        pickup_type=departure.pickup_type,
                        drop_off_type=arrival.drop_off_type,
                    )
                )

        # Deduplicate across calendar days by active service set.
        templates_by_service_set: dict[frozenset[str], tuple[ServiceConnectionTemplate, ...]] = {}

        while day <= end_date:
            active_services = frozenset(
                service_id
                for service_id, service in data.services.items()
                if service.runs_on(day) and service_id in templates_by_service
            )

            if active_services:
                shared_connections = templates_by_service_set.get(active_services)
                if shared_connections is None:
                    merged: list[ServiceConnectionTemplate] = []
                    for service_id in active_services:
                        merged.extend(templates_by_service[service_id])

                    merged.sort(
                        key=lambda connection: (
                            connection.departure_secs,
                            connection.arrival_secs,
                            connection.trip_id,
                            connection.departure_sequence,
                        )
                    )
                    shared_connections = tuple(merged)
                    templates_by_service_set[active_services] = shared_connections

                data.connections_by_service_day[day] = shared_connections

            day += timedelta(days=1)

        logger.debug(
            "Built connection index: %d service days, %d unique service sets, "
            "%d template connections, %d lookback days",
            len(data.connections_by_service_day),
            len(templates_by_service_set),
            sum(len(items) for items in templates_by_service_set.values()),
            lookback_days,
        )

    # -- id-based getters ----------------------------------------------------
    # These are the primary entry points for an MCP tool: GTFS is a relational
    # web of ids (stop_id, route_id, trip_id, service_id), and an LLM caller
    # will almost always be chaining "resolve a name/number to an id, then
    # look up everything keyed by that id." Each getter below does exactly
    # one such id -> data hop and returns plain dicts/dataclasses, never
    # raising on a missing id (returns None / [] instead) so a tool layer
    # can turn that into a clean "not found" message instead of a 500.

    def get_stop(self, stop_id: str) -> Stop | None:
        """Resolve a single stop_id to its Stop record."""
        return self.data.stops_by_id.get(stop_id)

    def get_all_stops(self) -> list[Stop]:
        """Return all stops."""
        return list(self.data.stops_by_id.values())

    def get_stop_group(self, group_id: str) -> StopGroup | None:
        """Resolve a group ID within this schedule."""
        return self.data.stop_groups_by_id.get(group_id)

    def get_all_stop_groups(self) -> list[StopGroup]:
        """Return every group, including those without service on a given day."""
        return list(self.data.stop_groups_by_id.values())

    def get_stops_for_group(self, group_id: str) -> list[Stop]:
        """Return the group's complete platform records, or [] for an unknown ID."""
        group = self.get_stop_group(group_id)
        if group is None:
            return []
        return [self.data.stops_by_id[stop_id] for stop_id in group.stop_ids]

    def get_route(self, route_id: str) -> Route | None:
        """Resolve a single route_id to its Route record."""
        return self.data.routes_by_id.get(route_id)

    def get_all_routes(self) -> list[Route]:
        """Return all routes."""
        return list(self.data.routes_by_id.values())

    def get_routes_for_stop(self, stop_id: str) -> list[Route]:
        """Every route that calls at a given stop_id."""
        route_ids = self.data.routes_by_stop.get(stop_id, set())
        return [self.data.routes_by_id[rid] for rid in route_ids if rid in self.data.routes_by_id]

    def get_stop_sequence_for_route(self, route_id: str, direction_id: int) -> list[Stop]:
        """The representative (most common) ordered stop pattern for a
        route_id + direction_id, as actual Stop records."""
        stop_ids = self.data.stops_by_route.get(route_id, {}).get(direction_id, [])
        return [self.data.stops_by_id[sid] for sid in stop_ids if sid in self.data.stops_by_id]

    # -- searches ---------------------------------------------------

    def fuzzy_search_stops(
        self,
        query: str,
        limit: int = 10,
        min_score: float = 0.5,
    ) -> list[StopMatch]:
        """
        Typo-tolerant, partial-match stop name search.

        An MCP caller (or the LLM driving it) rarely has an exact, correctly
        diacritic-typed stop name -- they have something like "dworzec" or
        "Poznan Glowny" or a slightly misspelled name. This combines:
          1. exact normalized match (score 1.0)
          2. substring match, either direction (score 0.9), so "głowny"
             finds "Poznań Główny" and "Poznań Główny peron 1" both
          3. fuzzy ratio via difflib.SequenceMatcher for everything else,
             filtered to >= min_score

        Returns results sorted by score desc, deduplicated by stop_id,
        capped at `limit`.
        """
        norm_query = normalize_name(query)
        if not norm_query:
            return []

        scored: dict[str, float] = {}

        for norm_name, group_ids in self.data.stop_group_ids_by_normalized_name.items():
            if norm_name == norm_query:
                score = 1.0
            elif norm_query in norm_name or norm_name in norm_query:
                score = 0.9
            else:
                score = SequenceMatcher(None, norm_query, norm_name).ratio()
                if score < min_score:
                    continue

            for group_id in group_ids:
                for stop_id in self.data.stop_groups_by_id[group_id].stop_ids:
                    scored[stop_id] = score

        ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        return [
            StopMatch(stop=self.data.stops_by_id[stop_id], score=score)
            for stop_id, score in ranked
            if stop_id in self.data.stops_by_id
        ]

    def search_routes(self, query: str, limit: int = 10) -> list[dict]:
        """Public route-search helper used by the MCP tool layer."""
        norm_query = normalize_name(query)
        if not norm_query:
            return []

        ranked: list[tuple[float, str]] = []
        for route in self.get_all_routes():
            candidates = (
                normalize_name(route.route_id),
                normalize_name(route.route_short_name),
                normalize_name(route.route_long_name),
            )

            best_score = 0.0
            for candidate in candidates:
                if not candidate:
                    continue
                if norm_query == candidate:
                    best_score = max(best_score, 1.0)
                elif norm_query in candidate or candidate in norm_query:
                    best_score = max(best_score, 0.9)
                else:
                    best_score = max(
                        best_score, SequenceMatcher(None, norm_query, candidate).ratio()
                    )

            if best_score > 0.45:
                ranked.append((best_score, route.route_id))

        ranked.sort(key=lambda item: item[0], reverse=True)
        results: list[dict] = []
        for score, route_id in ranked[:limit]:
            summary = self.get_route_summary(route_id)
            if summary is None:
                continue
            results.append({**summary, "score": score})
        return results

    # -- composite / "useful" getters ----------------------------------------
    # These chain the id-getters above into the shapes an MCP tool actually
    # wants to hand back to the LLM in one call, instead of making the tool
    # layer re-implement joins every time.

    def get_next_departures(
        self,
        stop_id: str,
        after_secs: int,
        day: date,
        limit: int = 10,
        route_id: str | None = None,
        direction_id: int | None = None,
    ) -> list[dict]:
        """
        Return up to `limit` upcoming departures from `stop_id` at/after
        `after_secs` on `day`, filtered to services that run that day.
        Includes a real `departure_datetime` (anchored to `day`) alongside
        the raw GTFS-style `departure_time` string, so callers needing a
        normal timestamp don't have to do the conversion themselves.
        """
        if limit < 1:
            return []
        data = self.data
        active = {sid for sid, service in data.services.items() if service.runs_on(day)}
        entries = data.departures_by_stop.get(stop_id, [])

        # binary search for the first entry >= after_secs
        idx = bisect_left(entries, (after_secs, "", -1))

        results = []
        for departure_secs, trip_id, stop_sequence in entries[idx:]:
            trip = data.trips_by_id[trip_id]
            if trip.service_id not in active:
                continue
            if route_id is not None and trip.route_id != route_id:
                continue
            if direction_id is not None and trip.direction_id != direction_id:
                continue
            route = data.routes_by_id.get(trip.route_id)
            results.append(
                {
                    "departure_time": secs_to_hms(departure_secs),
                    "departure_datetime": secs_to_datetime(departure_secs, day).isoformat(),
                    "trip_id": trip_id,
                    "route_id": trip.route_id,
                    "route_short_name": route.route_short_name if route else trip.route_id,
                    "trip_headsign": trip.trip_headsign,
                    "direction_id": trip.direction_id,
                    "stop_sequence": stop_sequence,
                }
            )
            if len(results) >= limit:
                break
        return results

    def get_route_summary(self, route_id: str) -> dict | None:
        """Route metadata plus both directions' stop patterns -- a common
        'tell me about route X' shape."""
        route = self.data.routes_by_id.get(route_id)
        if route is None:
            return None
        return {
            "route_id": route.route_id,
            "route_short_name": route.route_short_name,
            "route_long_name": route.route_long_name,
            "route_type": route.route_type,
            "directions": {
                direction_id: [
                    s.stop_name for s in self.get_stop_sequence_for_route(route_id, direction_id)
                ]
                for direction_id in self.data.stops_by_route.get(route_id, {})
            },
        }
