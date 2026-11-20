
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import Mapping
from zoneinfo import ZoneInfo

import requests
from google.transit import gtfs_realtime_pb2

from ini import ini
from logging_config import logger
from services.ztm_static_schedule import (
    StopTime,
    secs_to_datetime,
    secs_to_hms,
)
from services.ztm_schedule_repository import ScheduleRepository


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


@dataclass(frozen=True, slots=True)
class RealtimeSnapshot:
    fetched_at: datetime
    feed_timestamp: datetime | None
    trips: Mapping[tuple[str, date], RealtimeTripUpdate]


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

    def fetch_feed(self) -> gtfs_realtime_pb2.FeedMessage:
        """Download and parse the current GTFS-Realtime trip updates."""

        try:
            response = requests.get(
                self.RT_ENDPOINT,
                params={"file": "trip_updates.pb"},
                timeout=ini.request_timeout_seconds,
            )
            response.raise_for_status()

            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(response.content)

            return feed

        except Exception as exc:
            raise GTFSRealtimeError(
                f"Unable to fetch GTFS-Realtime feed: {exc}"
            ) from exc

    # -----------------------------------------------------------------------
    # Loading
    # -----------------------------------------------------------------------

    def refresh(self) -> RealtimeSnapshot:
        """
        Fetch realtime data and atomically replace the current snapshot.

        If fetching or parsing fails, the previous snapshot remains available.
        """
        with self._refresh_lock:
            logger.info("Fetching ZTM realtime delays")

            feed = self.fetch_feed()
            new_snapshot = self._build_snapshot(feed)

            with self._snapshot_lock:
                self._snapshot = new_snapshot

            logger.info(
                "Realtime delays refreshed: %s trips",
                len(new_snapshot.trips),
            )

            return new_snapshot

    def _build_snapshot(
        self,
        feed: gtfs_realtime_pb2.FeedMessage,
    ) -> RealtimeSnapshot:
        """Join realtime trip updates with the static schedule for each service day."""

        feed_timestamp = None
        if feed.HasField("header") and feed.header.HasField("timestamp"):
            feed_timestamp = datetime.fromtimestamp(
                feed.header.timestamp,
                tz=POZNAN_TZ,
            )

        trips: dict[tuple[str, date], RealtimeTripUpdate] = {}

        for entity in feed.entity:
            if not entity.HasField("trip_update"):
                continue

            trip_update = entity.trip_update
            descriptor = trip_update.trip
            trip_id = descriptor.trip_id

            if not trip_id:
                continue

            # GTFS-RT start_date identifies the service day.
            # The fallback is appropriate for a current-day feed, but see note below.
            if descriptor.start_date:
                try:
                    service_day = datetime.strptime(
                        descriptor.start_date,
                        "%Y%m%d",
                    ).date()
                except ValueError:
                    logger.warning(
                        "Invalid GTFS-RT start_date %r for trip %s",
                        descriptor.start_date,
                        trip_id,
                    )
                    continue
            else:
                service_day = datetime.now(POZNAN_TZ).date()
                logger.debug(
                    "GTFS-RT trip %s has no start_date; assuming service day %s",
                    trip_id,
                    service_day,
                )
                

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

            static_by_sequence = {
                stop.stop_sequence: stop
                for stop in static_stop_times
            }

            static_by_id: dict[str, list[StopTime]] = {}
            for stop in static_stop_times:
                static_by_id.setdefault(stop.stop_id, []).append(stop)

            updates: list[RealtimeStopUpdate] = []

            for realtime_stop in trip_update.stop_time_update:
                static_stop = None

                # stop_sequence is the preferred identifier.
                if realtime_stop.HasField("stop_sequence"):
                    static_stop = static_by_sequence.get(
                        realtime_stop.stop_sequence
                    )

                # Only use stop_id when it uniquely identifies an occurrence.
                if static_stop is None and realtime_stop.stop_id:
                    candidates = static_by_id.get(realtime_stop.stop_id, [])
                    if len(candidates) == 1:
                        static_stop = candidates[0]

                if static_stop is None:
                    continue

                if (
                    realtime_stop.stop_id
                    and realtime_stop.stop_id != static_stop.stop_id
                ):
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
                    predicted_departure_dt = (
                        scheduled_departure_dt + timedelta(seconds=departure_delay)
                    )

                    departure = (
                        predicted_departure_dt,
                        departure_delay,
                    )

                    departure_delay_estimated = True

                stop_relationship = (
                    gtfs_realtime_pb2.TripUpdate.StopTimeUpdate
                    .ScheduleRelationship.Name(
                        realtime_stop.schedule_relationship
                    )
                    if realtime_stop.HasField("schedule_relationship")
                    else "SCHEDULED"
                )

                # Retain SKIPPED and other meaningful relationship-only updates.
                if (
                    arrival is None
                    and departure is None
                    and stop_relationship == "SCHEDULED"
                ):
                    continue

                updates.append(
                    RealtimeStopUpdate(
                        trip_id=trip_id,
                        route_id=trip.route_id,
                        route_short_name=(
                            route.route_short_name if route else trip.route_id
                        ),
                        stop_id=static_stop.stop_id,
                        stop_name=stop.stop_name if stop else None,
                        stop_sequence=static_stop.stop_sequence,
                        scheduled_arrival=secs_to_hms(
                            static_stop.arrival_secs
                        ),
                        scheduled_departure=secs_to_hms(
                            static_stop.departure_secs
                        ),
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
                route_short_name=(
                    route.route_short_name if route else trip.route_id
                ),
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
                stop_updates=tuple(
                    sorted(updates, key=lambda item: item.stop_sequence)
                ),
                trip_schedule_relationship=trip_relationship,
            )

        return RealtimeSnapshot(
            fetched_at=datetime.now(POZNAN_TZ),
            feed_timestamp=feed_timestamp,
            trips=MappingProxyType(trips),
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

        return [
            trip
            for trip in snapshot.trips.values()
            if trip.route_id == route_id
        ]

    def get_stop_update(
        self,
        trip_id: str,
        stop_sequence: int,
        service_date: date,
    ) -> RealtimeStopUpdate | None:
        """Get the realtime update for a specific trip stop and service day."""

        trip_update = self.get_trip_updates(trip_id, service_date)

        if trip_update is None:
            return None

        return next(
            (
                update
                for update in trip_update.stop_updates
                if update.stop_sequence == stop_sequence
            ),
            None,
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
                    logger.exception(
                        "Realtime refresh failed; keeping previous snapshot"
                    )

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
