"""
In-memory storage for ZTM Poznań GTFS data.

Design notes
------------
- All raw GTFS rows are parsed once into small, slotted dataclasses
  (Stop, Route, Trip, StopTime, Service, FeedInfo).
- On top of those records we build a handful of index dicts/lists that
  directly answer the queries an MCP tool will need:
    * _stops_by_id          - exact stop lookup
    * _stops_by_name        - normalized name -> [stop_id, ...] (exact match)
    * _trips_by_id          - exact trip lookup
    * _trips_by_route       - route_id -> [trip_id, ...]
    * _stop_times_by_trip   - trip_id -> [StopTime, ...] sorted by stop_sequence
    * _departures_by_stop   - stop_id -> [(departure_secs, trip_id, stop_sequence), ...]
                               sorted by departure_secs (binary-searchable with bisect)
    * _services             - service_id -> Service (weekly pattern + exceptions)
    * _routes_by_stop       - stop_id -> {route_id, ...}
    * _stops_by_route       - route_id -> {direction_id -> [stop_id, ...]} (modal pattern)
- Everything lives inside a single GTFSData object. Reloading the feed
  means building a brand new GTFSData and swapping a module-level
  reference, so readers never see a half-built state.

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
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from collections import Counter
import threading


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hms_to_secs(value: str) -> int:
    """Convert 'HH:MM:SS' (possibly >24h, e.g. '25:30:00') to seconds since
    the start of the GTFS service day."""
    h, m, s = value.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


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
    return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))


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
    brigade: str


@dataclass(frozen=True, slots=True)
class StopTime:
    trip_id: str
    stop_id: str
    arrival_secs: int
    departure_secs: int
    stop_sequence: int
    stop_headsign: str
    pickup_type: int
    drop_off_type: int

    def arrival_dt(self, service_day: date) -> datetime:
        """Real, wall-clock-correct arrival datetime for a given service day."""
        return secs_to_datetime(self.arrival_secs, service_day)

    def departure_dt(self, service_day: date) -> datetime:
        """Real, wall-clock-correct departure datetime for a given service day."""
        return secs_to_datetime(self.departure_secs, service_day)


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

    def covers(self, day: date) -> bool:
        return self.start_date <= day <= self.end_date


@dataclass(frozen=True, slots=True)
class StopMatch:
    """A fuzzy/substring search hit, with a score so callers (and the LLM)
    can tell a confident match from a guess."""
    stop: Stop
    score: float  # 1.0 = exact normalized match, lower = weaker match


# ---------------------------------------------------------------------------
# Container with indexes
# ---------------------------------------------------------------------------

class ZTMStaticSchedule:
    _instance: "ZTMStaticSchedule | None" = None
    _lock = threading.Lock()

    @classmethod
    def instance(cls) -> "ZTMStaticSchedule":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self) -> None:
        self._feed_info: FeedInfo | None = None

        # Primary records keyed by their GTFS id.
        self._stops_by_id: dict[str, Stop] = {}
        self._routes_by_id: dict[str, Route] = {}
        self._trips_by_id: dict[str, Trip] = {}
        self._services: dict[str, Service] = {}

        # Derived indexes -- all private; access through the public getters.
        self._stops_by_name: dict[str, list[str]] = {}
        self._trips_by_route: dict[str, list[str]] = {}
        self._stop_times_by_trip: dict[str, list[StopTime]] = {}
        self._departures_by_stop: dict[str, list[tuple[int, str, int]]] = {}
        self._routes_by_stop: dict[str, set[str]] = {}
        self._stops_by_route: dict[str, dict[int, list[str]]] = {}

        # Pre-normalized stop names to avoid re-normalizing on every fuzzy query.
        self._normalized_names: dict[str, list[str]] = {}

    # -- properties for top-level metadata -----------------------------------

    @property
    def feed_info(self) -> FeedInfo | None:
        """Feed publisher metadata, or None if feed_info.txt was absent."""
        return self._feed_info

    # -- loading -----------------------------------------------------------

    @classmethod
    def load(cls, rows_by_file: dict[str, list[dict[str, str]]]) -> "ZTMStaticSchedule":
        storage = object.__new__(cls)
        storage.__init__()
        
        storage._load_feed_info(rows_by_file.get("feed_info", []))
        storage._load_stops(rows_by_file.get("stops", []))
        storage._load_routes(rows_by_file.get("routes", []))
        storage._load_trips(rows_by_file.get("trips", []))
        storage._load_calendar(rows_by_file.get("calendar", []))
        storage._load_calendar_dates(rows_by_file.get("calendar_dates", []))
        storage._load_stop_times(rows_by_file.get("stop_times", []))

        storage._build_departure_index()
        storage._build_route_stop_indexes()
        
        with cls._lock:
            cls._instance = storage
        print("ZTMStaticSchedule: Loaded GTFS data")
        return storage
    
    def _load_feed_info(self, rows: list[dict[str, str]]) -> None:
        if not rows:
            return
        row = rows[0]
        self._feed_info = FeedInfo(
            publisher_name=row["feed_publisher_name"],
            publisher_url=row["feed_publisher_url"],
            lang=row["feed_lang"],
            start_date=_parse_date(row["feed_start_date"]),
            end_date=_parse_date(row["feed_end_date"]),
        )

    def _load_stops(self, rows: list[dict[str, str]]) -> None:
        for row in rows:
            stop = Stop(
                stop_id=row["stop_id"].strip(),
                stop_code=row["stop_code"].strip(),
                stop_name=row["stop_name"].strip(),
                stop_lat=float(row["stop_lat"]),
                stop_lon=float(row["stop_lon"]),
                zone_id=row["zone_id"].strip(),
            )
            self._stops_by_id[stop.stop_id] = stop
            key = normalize_name(stop.stop_name)
            self._stops_by_name.setdefault(key, []).append(stop.stop_id)
            self._normalized_names.setdefault(key, []).append(stop.stop_id)

    def _load_routes(self, rows: list[dict[str, str]]) -> None:
        for row in rows:
            route = Route(
                route_id=row["route_id"].strip(),
                agency_id=row["agency_id"].strip(),
                route_short_name=row["route_short_name"].strip(),
                route_long_name=row["route_long_name"].strip(),
                route_type=int(row["route_type"]),
            )
            self._routes_by_id[route.route_id] = route

    def _load_trips(self, rows: list[dict[str, str]]) -> None:
        for row in rows:
            trip = Trip(
                trip_id=row["trip_id"].strip(),
                route_id=row["route_id"].strip(),
                service_id=row["service_id"].strip(),
                trip_headsign=row["trip_headsign"].strip(),
                direction_id=int(row["direction_id"]),
                brigade=row.get("brigade", "").strip(),
            )
            self._trips_by_id[trip.trip_id] = trip
            self._trips_by_route.setdefault(trip.route_id, []).append(trip.trip_id)

    def _load_calendar(self, rows: list[dict[str, str]]) -> None:
        if not rows:
            return
        for row in rows:
            service_id = row["service_id"].strip()
            pattern = tuple(
                row[day] == "1"
                for day in (
                    "monday", "tuesday", "wednesday", "thursday",
                    "friday", "saturday", "sunday",
                )
            )
            self._services[service_id] = Service(
                service_id=service_id,
                weekday_pattern=pattern,  # type: ignore[arg-type]
                start_date=_parse_date(row["start_date"]),
                end_date=_parse_date(row["end_date"]),
            )

    def _load_calendar_dates(self, rows: list[dict[str, str]]) -> None:
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
            existing = self._services.get(service_id)
            if existing is None:
                # service_id only defined via calendar_dates (no weekly pattern)
                self._services[service_id] = Service(
                    service_id=service_id,
                    weekday_pattern=(False,) * 7,
                    start_date=date.min,
                    end_date=date.max,
                    added_dates=extra_added,
                    removed_dates=extra_removed,
                )
            else:
                self._services[service_id] = Service(
                    service_id=existing.service_id,
                    weekday_pattern=existing.weekday_pattern,
                    start_date=existing.start_date,
                    end_date=existing.end_date,
                    added_dates=extra_added,
                    removed_dates=extra_removed,
                )

    def _load_stop_times(self, rows: list[dict[str, str]]) -> None:
        for row in rows:
            st = StopTime(
                trip_id=row["trip_id"].strip(),
                stop_id=row["stop_id"].strip(),
                arrival_secs=hms_to_secs(row["arrival_time"]),
                departure_secs=hms_to_secs(row["departure_time"]),
                stop_sequence=int(row["stop_sequence"]),
                stop_headsign=row["stop_headsign"].strip(),
                pickup_type=int(row["pickup_type"]),
                drop_off_type=int(row["drop_off_type"]),
            )
            self._stop_times_by_trip.setdefault(st.trip_id, []).append(st)

        for stop_times in self._stop_times_by_trip.values():
            stop_times.sort(key=lambda st: st.stop_sequence)

    def _build_departure_index(self) -> None:
        for trip_id, stop_times in self._stop_times_by_trip.items():
            for st in stop_times:
                self._departures_by_stop.setdefault(st.stop_id, []).append(
                    (st.departure_secs, trip_id, st.stop_sequence)
                )

        for entries in self._departures_by_stop.values():
            entries.sort(key=lambda e: e[0])

    def _build_route_stop_indexes(self) -> None:
        pattern_counts: dict[tuple[str, int], Counter[tuple[str, ...]]] = {}
        for trip_id, stop_times in self._stop_times_by_trip.items():
            trip = self._trips_by_id.get(trip_id)
            if trip is None:
                continue

            route_id = trip.route_id
            direction_id = trip.direction_id

            stop_ids = tuple(st.stop_id for st in stop_times)

            # _routes_by_stop
            for stop_id in stop_ids:
                self._routes_by_stop.setdefault(stop_id, set()).add(route_id)

            # count route patterns
            pattern_counts.setdefault((route_id, direction_id), Counter())[stop_ids] += 1

        # select most common pattern per route+direction
        for (route_id, direction_id), counter in pattern_counts.items():
            most_common_pattern, _ = counter.most_common(1)[0]

            self._stops_by_route.setdefault(route_id, {})[direction_id] = list(
                most_common_pattern
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
        return self._stops_by_id.get(stop_id)
    
    def get_all_stops(self) -> list[Stop]:
        """Return all stops."""
        return list(self._stops_by_id.values())

    def get_route(self, route_id: str) -> Route | None:
        """Resolve a single route_id to its Route record."""
        return self._routes_by_id.get(route_id)
    
    def get_all_routes(self) -> list[Route]:
        """Return all routes."""
        return list(self._routes_by_id.values())

    def get_trip(self, trip_id: str) -> Trip | None:
        """Resolve a single trip_id to its Trip record."""
        return self._trips_by_id.get(trip_id)

    def get_service(self, service_id: str) -> Service | None:
        return self._services.get(service_id)

    def get_trips_for_route(self, route_id: str, direction_id: int | None = None) -> list[Trip]:
        """All trips belonging to a route, optionally filtered to one direction."""
        trip_ids = self._trips_by_route.get(route_id, [])
        trips = [self._trips_by_id[tid] for tid in trip_ids if tid in self._trips_by_id]
        if direction_id is not None:
            trips = [t for t in trips if t.direction_id == direction_id]
        return trips

    def get_stop_times_for_trip(self, trip_id: str) -> list[StopTime]:
        """Full ordered itinerary (by stop_sequence) for one trip_id."""
        return self._stop_times_by_trip.get(trip_id, [])

    def get_routes_for_stop(self, stop_id: str) -> list[Route]:
        """Every route that calls at a given stop_id."""
        route_ids = self._routes_by_stop.get(stop_id, set())
        return [self._routes_by_id[rid] for rid in route_ids if rid in self._routes_by_id]

    def get_stop_sequence_for_route(self, route_id: str, direction_id: int) -> list[Stop]:
        """The representative (most common) ordered stop pattern for a
        route_id + direction_id, as actual Stop records."""
        stop_ids = self._stops_by_route.get(route_id, {}).get(direction_id, [])
        return [self._stops_by_id[sid] for sid in stop_ids if sid in self._stops_by_id]

    def get_active_services(self, day: date) -> set[str]:
        """All service_ids running on a given calendar date."""
        return {sid for sid, svc in self._services.items() if svc.runs_on(day)}

    def get_stops_by_name(self, name: str) -> list[Stop]:
        """Exact (post-normalization) name lookup. Use fuzzy_search_stops
        for typo-tolerant / partial matching instead."""
        key = normalize_name(name)
        ids = self._stops_by_name.get(key, [])
        return [self._stops_by_id[i] for i in ids]

    # -- searches ---------------------------------------------------
    
    def find_routes_by_short_name(self, short_name: str) -> list[Route]:
        """Resolve a human-supplied line number ('15', '171') to Route records."""
        norm = short_name.strip().upper()
        return [
            r for r in self._routes_by_id.values()
            if r.route_short_name.upper() == norm
        ]

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

        for norm_name, stop_ids in self._normalized_names.items():
            if norm_name == norm_query:
                score = 1.0
            elif norm_query in norm_name or norm_name in norm_query:
                score = 0.9
            else:
                score = SequenceMatcher(None, norm_query, norm_name).ratio()
                if score < min_score:
                    continue

            for stop_id in stop_ids:
                if score > scored.get(stop_id, 0.0):
                    scored[stop_id] = score

        ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        return [
            StopMatch(stop=self._stops_by_id[stop_id], score=score)
            for stop_id, score in ranked
            if stop_id in self._stops_by_id
        ]

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
    ) -> list[dict]:
        """
        Return up to `limit` upcoming departures from `stop_id` at/after
        `after_secs` on `day`, filtered to services that run that day.
        Includes a real `departure_datetime` (anchored to `day`) alongside
        the raw GTFS-style `departure_time` string, so callers needing a
        normal timestamp don't have to do the conversion themselves.
        """
        active = self.get_active_services(day)
        entries = self._departures_by_stop.get(stop_id, [])

        # binary search for the first entry >= after_secs
        idx = bisect_left(entries, (after_secs, "", -1))

        results = []
        for departure_secs, trip_id, stop_sequence in entries[idx:]:
            trip = self._trips_by_id[trip_id]
            if trip.service_id not in active:
                continue
            route = self._routes_by_id.get(trip.route_id)
            results.append({
                "departure_time": secs_to_hms(departure_secs),
                "departure_datetime": secs_to_datetime(departure_secs, day).isoformat(),
                "trip_id": trip_id,
                "route_id": trip.route_id,
                "route_short_name": route.route_short_name if route else trip.route_id,
                "trip_headsign": trip.trip_headsign,
                "stop_sequence": stop_sequence,
            })
            if len(results) >= limit:
                break
        return results

    def get_trip_summary(self, trip_id: str, service_day: date | None = None) -> list[dict]:
        """
        Full summary of a trip, in stop_sequence order. If `service_day`
        is given, includes real arrival/departure datetimes anchored to
        that date; otherwise only the raw HH:MM:SS strings are included
        (since without a date, "25:30:00" can't be turned into a real
        timestamp).
        """
        result = []
        for st in self.get_stop_times_for_trip(trip_id):
            stop = self._stops_by_id.get(st.stop_id)
            entry = {
                "stop_id": st.stop_id,
                "stop_name": stop.stop_name if stop else None,
                "arrival_time": secs_to_hms(st.arrival_secs),
                "departure_time": secs_to_hms(st.departure_secs),
                "stop_sequence": st.stop_sequence,
            }
            if service_day is not None:
                entry["arrival_datetime"] = st.arrival_dt(service_day).isoformat()
                entry["departure_datetime"] = st.departure_dt(service_day).isoformat()
            result.append(entry)
        return result

    def get_route_summary(self, route_id: str) -> dict | None:
        """Route metadata plus both directions' stop patterns -- a common
        'tell me about route X' shape."""
        route = self._routes_by_id.get(route_id)
        if route is None:
            return None
        return {
            "route_id": route.route_id,
            "route_short_name": route.route_short_name,
            "route_long_name": route.route_long_name,
            "route_type": route.route_type,
            "directions": {
                direction_id: [s.stop_name for s in self.get_stop_sequence_for_route(route_id, direction_id)]
                for direction_id in self._stops_by_route.get(route_id, {})
            },
        }