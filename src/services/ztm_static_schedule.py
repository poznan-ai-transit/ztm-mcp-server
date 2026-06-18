"""
In-memory storage for ZTM Poznań GTFS data.

Design notes
------------
- All raw GTFS rows are parsed once into small, slotted dataclasses
  (Stop, Route, Trip, StopTime, Service, FeedInfo).
- On top of those records we build a handful of index dicts/lists that
  directly answer the queries an MCP tool will need:
    * stops_by_id          - exact stop lookup
    * stops_by_name        - normalized name -> [stop_id, ...]
    * trips_by_id          - exact trip lookup
    * trips_by_route       - route_id -> [trip_id, ...]
    * stop_times_by_trip   - trip_id -> [StopTime, ...] sorted by stop_sequence
    * departures_by_stop   - stop_id -> [(departure_secs, trip_id, stop_sequence), ...]
                              sorted by departure_secs (binary-searchable with bisect)
    * services             - service_id -> Service (weekly pattern + exceptions)
- Everything lives inside a single GTFSData object. Reloading the feed
  means building a brand new GTFSData and swapping a module-level
  reference, so readers never see a half-built state.
"""

from __future__ import annotations

import unicodedata
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date
from collections import Counter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def hms_to_secs(value: str) -> int:
    """Convert 'HH:MM:SS' (possibly >24h, e.g. '25:30:00') to seconds since midnight."""
    h, m, s = value.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def secs_to_hms(secs: int) -> str:
    """Inverse of parse_time_to_secs, for display."""
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def normalize_name(name: str) -> str:
    """Lowercase, strip whitespace/diacritics for fuzzy stop-name matching."""
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


# ---------------------------------------------------------------------------
# Container with indexes
# ---------------------------------------------------------------------------

class ZTMStaticSchedule:
    _instance = None

    @classmethod
    def instance(cls) -> "ZTMStaticSchedule":
        return cls._instance

    def __new__(cls) -> ZTMStaticSchedule:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self.feed_info: FeedInfo | None = None

        self.stops_by_id: dict[str, Stop] = {}
        self.routes_by_id: dict[str, Route] = {}
        self.trips_by_id: dict[str, Trip] = {}
        self.services: dict[str, Service] = {}

        self.stops_by_name: dict[str, list[str]] = {}
        self.trips_by_route: dict[str, list[str]] = {}
        self.stop_times_by_trip: dict[str, list[StopTime]] = {}
        self.departures_by_stop: dict[str, list[tuple[int, str, int]]] = {}
        
        self.routes_by_stop: dict[str, set[str]] = {}
        self.stops_by_route: dict[str, dict[int, list[str]]] = {}

    # -- loading -----------------------------------------------------------

    @classmethod
    def load(cls, rows_by_file: dict[str, list[dict[str, str]]]) -> "ZTMStaticSchedule":
        storage = cls.__new__(cls)
        storage._initialized = False
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
        cls._instance = storage
        return storage

    def _load_feed_info(self, rows: list[dict[str, str]]) -> None:
        if not rows:
            return
        row = rows[0]
        self.feed_info = FeedInfo(
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
            self.stops_by_id[stop.stop_id] = stop
            key = normalize_name(stop.stop_name)
            self.stops_by_name.setdefault(key, []).append(stop.stop_id)

    def _load_routes(self, rows: list[dict[str, str]]) -> None:
        for row in rows:
            route = Route(
                route_id=row["route_id"].strip(),
                agency_id=row["agency_id"].strip(),
                route_short_name=row["route_short_name"].strip(),
                route_long_name=row["route_long_name"].strip(),
                route_type=int(row["route_type"]),
            )
            self.routes_by_id[route.route_id] = route

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
            self.trips_by_id[trip.trip_id] = trip
            self.trips_by_route.setdefault(trip.route_id, []).append(trip.trip_id)

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
            self.services[service_id] = Service(
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
            existing = self.services.get(service_id)
            if existing is None:
                # service_id only defined via calendar_dates (no weekly pattern)
                self.services[service_id] = Service(
                    service_id=service_id,
                    weekday_pattern=(False,) * 7,
                    start_date=date.min,
                    end_date=date.max,
                    added_dates=extra_added,
                    removed_dates=extra_removed,
                )
            else:
                self.services[service_id] = Service(
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
            self.stop_times_by_trip.setdefault(st.trip_id, []).append(st)

        for stop_times in self.stop_times_by_trip.values():
            stop_times.sort(key=lambda st: st.stop_sequence)

    def _build_departure_index(self) -> None:
        for trip_id, stop_times in self.stop_times_by_trip.items():
            for st in stop_times:
                if st.pickup_type == 1:
                    # pickup_type 1 == "no pickup at this stop"
                    continue
                self.departures_by_stop.setdefault(st.stop_id, []).append(
                    (st.departure_secs, trip_id, st.stop_sequence)
                )

        for entries in self.departures_by_stop.values():
            entries.sort(key=lambda e: e[0])
            
    def _build_route_stop_indexes(self) -> None:
        pattern_counts: dict[tuple[str, int], Counter[tuple[str, ...]]] = {}
        for trip_id, stop_times in self.stop_times_by_trip.items():
            trip = self.trips_by_id.get(trip_id)
            if trip is None:
                continue

            route_id = trip.route_id
            direction_id = trip.direction_id

            stop_ids = tuple(st.stop_id for st in stop_times)

            # routes_by_stop
            for stop_id in stop_ids:
                self.routes_by_stop.setdefault(stop_id, set()).add(route_id)

            # count route patterns
            pattern_counts[(route_id, direction_id)][stop_ids] += 1

        # select most common pattern per route+direction
        for (route_id, direction_id), counter in pattern_counts.items():
            most_common_pattern, _ = counter.most_common(1)[0]

            self.stops_by_route.setdefault(route_id, {})[direction_id] = list(
                most_common_pattern
            )

    # -- queries -------------------------------------------------------------

    def active_services(self, day: date) -> set[str]:
        return {sid for sid, svc in self.services.items() if svc.runs_on(day)}

    def find_stops_by_name(self, name: str) -> list[Stop]:
        key = normalize_name(name)
        ids = self.stops_by_name.get(key, [])
        return [self.stops_by_id[i] for i in ids]

    def next_departures(
        self,
        stop_id: str,
        after_secs: int,
        day: date,
        limit: int = 10,
    ) -> list[dict]:
        """
        Return up to `limit` upcoming departures from `stop_id` at/after
        `after_secs` on `day`, filtered to services that run that day.
        """
        active = self.active_services(day)
        entries = self.departures_by_stop.get(stop_id, [])

        # binary search for the first entry >= after_secs
        idx = bisect_left(entries, (after_secs, "", -1))

        results = []
        for departure_secs, trip_id, stop_sequence in entries[idx:]:
            trip = self.trips_by_id[trip_id]
            if trip.service_id not in active:
                continue
            route = self.routes_by_id.get(trip.route_id)
            results.append({
                "departure_time": secs_to_hms(departure_secs),
                "departure_secs": departure_secs,
                "trip_id": trip_id,
                "route_short_name": route.route_short_name if route else trip.route_id,
                "trip_headsign": trip.trip_headsign,
                "stop_sequence": stop_sequence,
            })
            if len(results) >= limit:
                break
        return results

    def trip_stops(self, trip_id: str) -> list[dict]:
        """Full itinerary of a trip, in order."""
        result = []
        for st in self.stop_times_by_trip.get(trip_id, []):
            stop = self.stops_by_id.get(st.stop_id)
            result.append({
                "stop_id": st.stop_id,
                "stop_name": stop.stop_name if stop else None,
                "arrival_time": secs_to_hms(st.arrival_secs),
                "departure_time": secs_to_hms(st.departure_secs),
                "stop_sequence": st.stop_sequence,
            })
        return result