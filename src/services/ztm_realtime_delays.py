from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import MappingProxyType
from zoneinfo import ZoneInfo

import requests
from google.transit import gtfs_realtime_pb2

from ini import ini
from logging_config import logger
from services.ztm_schedule_repository import ScheduleRepository
from services.ztm_static_schedule import (
    StopTime,
    secs_to_datetime,
    secs_to_hms,
)

POZNAN_TZ = ZoneInfo("Europe/Warsaw")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GTFSRealtimeError(RuntimeError):
    """Raised when the realtime GTFS feed cannot be fetched or parsed."""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RealtimeStopUpdate:
    trip_id: str
    route_id: str
    route_short_name: str
    stop_id: str
    stop_name: str | None
    stop_sequence: int

    scheduled_arrival: str
    scheduled_departure: str

    predicted_arrival: datetime | None
    predicted_departure: datetime | None

    arrival_delay: int | None
    departure_delay: int | None
    departure_delay_estimated: bool

    schedule_relationship: str


@dataclass(frozen=True, slots=True)
class RealtimeTripUpdate:
    trip_id: str
    route_id: str
    route_short_name: str
    trip_headsign: str
    service_day: date
    timestamp: datetime | None
    stop_updates: tuple[RealtimeStopUpdate, ...]
    trip_schedule_relationship: str
    vehicle_id: str | None = None


@dataclass(frozen=True, slots=True)
class RealtimeSnapshot:
    fetched_at: datetime
    feed_timestamp: datetime | None
    trips: Mapping[tuple[str, date], RealtimeTripUpdate]
    vehicles_by_trip: Mapping[tuple[str, date], str] = MappingProxyType({})
    vehicles_by_trip_id: Mapping[str, str] = MappingProxyType({})
    vehicles_by_base_trip_id: Mapping[str, str] = MappingProxyType({})


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ZTMRealtimeDelays:
    BASE_URL = "https://www.ztm.poznan.pl"
    RT_ENDPOINT = f"{BASE_URL}/pl/dla-deweloperow/getGtfsRtFile"

    def __init__(self, repository: ScheduleRepository) -> None:
        self.repository = repository

        self._snapshot_lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._snapshot: RealtimeSnapshot | None = None

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # -----------------------------------------------------------------------
    # Fetching
    # -----------------------------------------------------------------------

    def _fetch_pb_file(self, filename: str) -> gtfs_realtime_pb2.FeedMessage:
        try:
            response = requests.get(
                self.RT_ENDPOINT,
                params={"file": filename},
                timeout=ini.request_timeout_seconds,
            )
            response.raise_for_status()

            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(response.content)
            return feed
        except Exception as exc:
            raise GTFSRealtimeError(
                f"Unable to fetch GTFS-Realtime feed {filename}: {exc}"
            ) from exc

    def fetch_feed(self) -> gtfs_realtime_pb2.FeedMessage:
        """Download and parse the current GTFS-Realtime trip updates."""
        return self._fetch_pb_file("trip_updates.pb")

    def fetch_vehicle_positions(self) -> gtfs_realtime_pb2.FeedMessage:
        """Download and parse the current GTFS-Realtime vehicle positions."""
        return self._fetch_pb_file("vehicle_positions.pb")

    # -----------------------------------------------------------------------
    # Loading
    # -----------------------------------------------------------------------

    def refresh(self) -> RealtimeSnapshot:
        """
        Fetch realtime data and atomically replace the current snapshot.

        If fetching or parsing trip updates fails, the previous snapshot remains available.
        """
        with self._refresh_lock:
            logger.info("Fetching ZTM realtime delays and vehicle positions")

            feed = self.fetch_feed()
            vehicle_feed: gtfs_realtime_pb2.FeedMessage | None = None
            try:
                vehicle_feed = self.fetch_vehicle_positions()
            except GTFSRealtimeError:
                logger.warning(
                    "Unable to fetch vehicle_positions.pb; continuing with trip updates only"
                )

            new_snapshot = self._build_snapshot(feed, vehicle_feed)

            with self._snapshot_lock:
                self._snapshot = new_snapshot

            logger.info(
                "Realtime refreshed: %s trips, %s active trip-vehicle mappings",
                len(new_snapshot.trips),
                len(new_snapshot.vehicles_by_trip),
            )

            return new_snapshot

    @staticmethod
    def _resolve_service_day(
        descriptor: gtfs_realtime_pb2.TripDescriptor,
        default_day: date,
    ) -> date | None:
        if descriptor.start_date:
            try:
                return datetime.strptime(descriptor.start_date, "%Y%m%d").date()
            except ValueError:
                logger.warning(
                    "Invalid GTFS-RT start_date %r for trip %s",
                    descriptor.start_date,
                    descriptor.trip_id,
                )
                return None
        return default_day

    @staticmethod
    def _base_trip_id(trip_id: str) -> str:
        return trip_id.split("^", 1)[0]

    def _build_snapshot(
        self,
        feed: gtfs_realtime_pb2.FeedMessage,
        vehicle_feed: gtfs_realtime_pb2.FeedMessage | None = None,
    ) -> RealtimeSnapshot:
        """Join realtime trip updates and vehicle positions with the static schedule."""

        feed_timestamp = None
        if feed.HasField("header") and feed.header.HasField("timestamp"):
            feed_timestamp = datetime.fromtimestamp(
                feed.header.timestamp,
                tz=POZNAN_TZ,
            )

        today_local = datetime.now(POZNAN_TZ).date()
        vehicles_by_trip: dict[tuple[str, date], str] = {}
        vehicles_by_trip_id: dict[str, str] = {}
        vehicles_by_base_trip_id: dict[str, str] = {}

        if vehicle_feed is not None:
            for entity in vehicle_feed.entity:
                if not entity.HasField("vehicle"):
                    continue
                vehicle_pos = entity.vehicle
                if not vehicle_pos.HasField("trip") or not vehicle_pos.HasField("vehicle"):
                    continue

                trip_id = vehicle_pos.trip.trip_id.strip()
                vehicle_id = vehicle_pos.vehicle.id.strip()
                if not trip_id or not vehicle_id:
                    continue

                service_day = self._resolve_service_day(vehicle_pos.trip, today_local)
                if service_day is None:
                    continue

                vehicles_by_trip[(trip_id, service_day)] = vehicle_id
                vehicles_by_trip_id[trip_id] = vehicle_id
                vehicles_by_base_trip_id[self._base_trip_id(trip_id)] = vehicle_id

        trips: dict[tuple[str, date], RealtimeTripUpdate] = {}

        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue

            trip_update = entity.trip_update
            descriptor = trip_update.trip
            trip_id = descriptor.trip_id

            if not trip_id:
                continue

            service_day = self._resolve_service_day(descriptor, today_local)
            if service_day is None:
                continue

            # Extract vehicle_id from trip_update if present, and backfill vehicles_by_trip.
            tu_vehicle_id: str | None = None
            if trip_update.HasField("vehicle") and trip_update.vehicle.id.strip():
                tu_vehicle_id = trip_update.vehicle.id.strip()
                vehicles_by_trip.setdefault((trip_id, service_day), tu_vehicle_id)

            # Each trip may belong to a different static GTFS archive.
            try:
                schedule = self.repository.for_date(service_day)
            except ValueError:
                logger.debug(
                    "No static schedule available for realtime trip %s on %s",
                    trip_id,
                    service_day,
                )
                continue

            data = schedule.data
            trip = data.trips_by_id.get(trip_id)

            if trip is None:
                logger.debug(
                    "Realtime trip %s not found in static schedule for %s",
                    trip_id,
                    service_day,
                )
                continue

            static_stop_times = data.stop_times_by_trip.get(trip_id, ())
            if not static_stop_times:
                continue

            route = data.routes_by_id.get(trip.route_id)

            static_by_sequence = {stop.stop_sequence: stop for stop in static_stop_times}

            static_by_id: dict[str, list[StopTime]] = {}
            for stop in static_stop_times:
                static_by_id.setdefault(stop.stop_id, []).append(stop)

            updates: list[RealtimeStopUpdate] = []

            for realtime_stop in trip_update.stop_time_update:
                static_stop = None

                # stop_sequence is the preferred identifier.
                if realtime_stop.HasField("stop_sequence"):
                    static_stop = static_by_sequence.get(realtime_stop.stop_sequence)

                # Only use stop_id when it uniquely identifies an occurrence.
                if static_stop is None and realtime_stop.stop_id:
                    candidates = static_by_id.get(realtime_stop.stop_id, [])
                    if len(candidates) == 1:
                        static_stop = candidates[0]

                if static_stop is None:
                    continue

                if realtime_stop.stop_id and realtime_stop.stop_id != static_stop.stop_id:
                    logger.warning(
                        "GTFS-RT stop mismatch for trip %s sequence %s",
                        trip_id,
                        static_stop.stop_sequence,
                    )
                    continue

                stop = data.stops_by_id.get(static_stop.stop_id)

                arrival = self._predict_event(
                    realtime_stop,
                    "arrival",
                    static_stop.arrival_secs,
                    service_day,
                )

                departure = self._predict_event(
                    realtime_stop,
                    "departure",
                    static_stop.departure_secs,
                    service_day,
                )

                departure_delay_estimated = False

                # ZTM provides only arrival.delay.
                # If no explicit departure prediction exists, assume the same delay
                # applies to departure, preserving the scheduled dwell time.
                if departure is None and arrival is not None:
                    scheduled_departure_dt = self._scheduled_datetime(
                        static_stop.departure_secs,
                        service_day,
                    )

                    departure_delay = arrival[1]
                    predicted_departure_dt = scheduled_departure_dt + timedelta(
                        seconds=departure_delay
                    )

                    departure = (
                        predicted_departure_dt,
                        departure_delay,
                    )

                    departure_delay_estimated = True

                stop_relationship = (
                    gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.ScheduleRelationship.Name(
                        realtime_stop.schedule_relationship
                    )
                    if realtime_stop.HasField("schedule_relationship")
                    else "SCHEDULED"
                )

                # Retain SKIPPED, NO_DATA, and meaningful prediction updates.
                if arrival is None and departure is None and stop_relationship == "SCHEDULED":
                    continue

                updates.append(
                    RealtimeStopUpdate(
                        trip_id=trip_id,
                        route_id=trip.route_id,
                        route_short_name=(route.route_short_name if route else trip.route_id),
                        stop_id=static_stop.stop_id,
                        stop_name=stop.stop_name if stop else None,
                        stop_sequence=static_stop.stop_sequence,
                        scheduled_arrival=secs_to_hms(static_stop.arrival_secs),
                        scheduled_departure=secs_to_hms(static_stop.departure_secs),
                        predicted_arrival=arrival[0] if arrival else None,
                        predicted_departure=departure[0] if departure else None,
                        arrival_delay=arrival[1] if arrival else None,
                        departure_delay=departure[1] if departure else None,
                        departure_delay_estimated=departure_delay_estimated,
                        schedule_relationship=stop_relationship,
                    )
                )

            trip_relationship = (
                gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship.Name(
                    descriptor.schedule_relationship
                )
                if descriptor.HasField("schedule_relationship")
                else "SCHEDULED"
            )

            # A cancelled trip can have no stop-time updates at all.
            if not updates and trip_relationship == "SCHEDULED":
                continue

            key = (trip_id, service_day)

            trips[key] = RealtimeTripUpdate(
                trip_id=trip_id,
                route_id=trip.route_id,
                route_short_name=(route.route_short_name if route else trip.route_id),
                trip_headsign=trip.trip_headsign,
                service_day=service_day,
                timestamp=(
                    datetime.fromtimestamp(
                        trip_update.timestamp,
                        tz=POZNAN_TZ,
                    )
                    if trip_update.HasField("timestamp")
                    else None
                ),
                stop_updates=tuple(sorted(updates, key=lambda item: item.stop_sequence)),
                trip_schedule_relationship=trip_relationship,
                vehicle_id=vehicles_by_trip.get(key) or tu_vehicle_id,
            )

        return RealtimeSnapshot(
            fetched_at=datetime.now(POZNAN_TZ),
            feed_timestamp=feed_timestamp,
            trips=MappingProxyType(trips),
            vehicles_by_trip=MappingProxyType(vehicles_by_trip),
            vehicles_by_trip_id=MappingProxyType(vehicles_by_trip_id),
            vehicles_by_base_trip_id=MappingProxyType(vehicles_by_base_trip_id),
        )

    # -----------------------------------------------------------------------
    # Prediction
    # -----------------------------------------------------------------------

    @staticmethod
    def _scheduled_datetime(
        scheduled_secs: int,
        service_day: date,
    ) -> datetime:
        scheduled_dt = secs_to_datetime(scheduled_secs, service_day)

        if scheduled_dt.tzinfo is None:
            return scheduled_dt.replace(tzinfo=POZNAN_TZ)

        return scheduled_dt.astimezone(POZNAN_TZ)

    @staticmethod
    def _predict_event(
        realtime_stop: gtfs_realtime_pb2.TripUpdate.StopTimeUpdate,
        event_name: str,
        scheduled_secs: int,
        service_day: date,
    ) -> tuple[datetime, int] | None:
        """
        Return (predicted_datetime, delay_seconds).

        GTFS-RT event.time is an absolute Unix timestamp.
        GTFS-RT event.delay is an offset from the scheduled event.
        """

        if not realtime_stop.HasField(event_name):
            return None

        event = getattr(realtime_stop, event_name)

        scheduled_dt = ZTMRealtimeDelays._scheduled_datetime(
            scheduled_secs,
            service_day,
        )

        if event.HasField("time"):
            predicted_dt = datetime.fromtimestamp(
                event.time,
                tz=POZNAN_TZ,
            )

            delay = int((predicted_dt - scheduled_dt).total_seconds())
            return predicted_dt, delay

        if event.HasField("delay"):
            delay = event.delay
            predicted_dt = scheduled_dt + timedelta(seconds=delay)
            return predicted_dt, delay

        return None

    # -----------------------------------------------------------------------
    # Snapshot access
    # -----------------------------------------------------------------------

    @property
    def snapshot(self) -> RealtimeSnapshot | None:
        """Return the latest successfully loaded snapshot."""

        with self._snapshot_lock:
            return self._snapshot

    def get_vehicle_id(
        self,
        trip_id: str,
        service_date: date,
    ) -> str | None:
        """Return the live vehicle ID assigned to (trip_id, service_date), even with no delay."""
        snapshot = self.snapshot
        if snapshot is None:
            return None
        return (
            snapshot.vehicles_by_trip.get((trip_id, service_date))
            or snapshot.vehicles_by_trip_id.get(trip_id)
            or snapshot.vehicles_by_base_trip_id.get(self._base_trip_id(trip_id))
        )

    def get_trip_updates(
        self,
        trip_id: str,
        service_date: date,
    ) -> RealtimeTripUpdate | None:
        snapshot = self.snapshot

        if snapshot is None:
            return None

        return snapshot.trips.get((trip_id, service_date))

    def get_all_trip_updates(self) -> list[RealtimeTripUpdate]:
        snapshot = self.snapshot

        if snapshot is None:
            return []

        return list(snapshot.trips.values())

    def get_delays_for_stop(
        self,
        stop_id: str,
    ) -> list[RealtimeStopUpdate]:
        snapshot = self.snapshot

        if snapshot is None:
            return []

        return [
            update
            for trip in snapshot.trips.values()
            for update in trip.stop_updates
            if update.stop_id == stop_id
        ]

    def get_delays_for_route(
        self,
        route_id: str,
    ) -> list[RealtimeTripUpdate]:
        snapshot = self.snapshot

        if snapshot is None:
            return []

        return [trip for trip in snapshot.trips.values() if trip.route_id == route_id]

    def get_stop_update(
        self,
        trip_id: str,
        stop_sequence: int,
        service_date: date,
    ) -> RealtimeStopUpdate | None:
        """Get the realtime update for a specific trip stop and service day.

        Follows GTFS-Realtime propagation rules: if no explicit update exists
        for ``stop_sequence``, the delay from the latest preceding stop update
        on the same trip is propagated forward unless cleared by ``NO_DATA``.
        """
        trip_update = self.get_trip_updates(trip_id, service_date)
        if trip_update is None:
            return None

        preceding_update: RealtimeStopUpdate | None = None
        for update in trip_update.stop_updates:
            if update.stop_sequence == stop_sequence:
                return update
            if update.stop_sequence < stop_sequence:
                preceding_update = update
            else:
                break

        if preceding_update is None:
            return None

        if preceding_update.schedule_relationship == "NO_DATA":
            return None

        propagated_delay = (
            preceding_update.departure_delay
            if preceding_update.departure_delay is not None
            else preceding_update.arrival_delay
        )
        if propagated_delay is None:
            return None

        try:
            schedule = self.repository.for_date(service_date)
        except ValueError:
            return None

        data = schedule.data
        static_stop = next(
            (
                st
                for st in data.stop_times_by_trip.get(trip_id, ())
                if st.stop_sequence == stop_sequence
            ),
            None,
        )
        if static_stop is None:
            return None

        stop = data.stops_by_id.get(static_stop.stop_id)
        scheduled_arrival_dt = self._scheduled_datetime(
            static_stop.arrival_secs,
            service_date,
        )
        scheduled_departure_dt = self._scheduled_datetime(
            static_stop.departure_secs,
            service_date,
        )
        delay_delta = timedelta(seconds=propagated_delay)

        return RealtimeStopUpdate(
            trip_id=trip_id,
            route_id=trip_update.route_id,
            route_short_name=trip_update.route_short_name,
            stop_id=static_stop.stop_id,
            stop_name=stop.stop_name if stop else None,
            stop_sequence=stop_sequence,
            scheduled_arrival=secs_to_hms(static_stop.arrival_secs),
            scheduled_departure=secs_to_hms(static_stop.departure_secs),
            predicted_arrival=scheduled_arrival_dt + delay_delta,
            predicted_departure=scheduled_departure_dt + delay_delta,
            arrival_delay=propagated_delay,
            departure_delay=propagated_delay,
            departure_delay_estimated=True,
            schedule_relationship="SCHEDULED",
        )

    # -----------------------------------------------------------------------
    # Background refresh
    # -----------------------------------------------------------------------

    def start_auto_refresh(
        self,
        interval_seconds: int = 30,
    ) -> None:
        """Start periodic realtime feed updates."""

        if interval_seconds < 1:
            raise ValueError("interval_seconds must be positive")

        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Realtime refresh is already running")

        self._stop_event.clear()

        def _runner() -> None:
            while not self._stop_event.is_set():
                try:
                    self.refresh()
                except Exception:
                    logger.exception("Realtime refresh failed; keeping previous snapshot")

                self._stop_event.wait(timeout=interval_seconds)

        self._thread = threading.Thread(
            target=_runner,
            name="ztm-realtime-delays",
            daemon=True,
        )

        self._thread.start()
        logger.info("Realtime refresh worker started")

    def stop_auto_refresh(self) -> None:
        self._stop_event.set()

        if self._thread is not None:
            self._thread.join()
            self._thread = None

        logger.info("Realtime refresh worker stopped")