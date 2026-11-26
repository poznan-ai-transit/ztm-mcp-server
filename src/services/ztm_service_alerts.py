from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from google.transit import gtfs_realtime_pb2

from ini import ini
from logging_config import logger

POZNAN_TZ = ZoneInfo("Europe/Warsaw")

CAUSE_NAMES: dict[int, str] = {value: name for name, value in gtfs_realtime_pb2.Alert.Cause.items()}
EFFECT_NAMES: dict[int, str] = {
    value: name for name, value in gtfs_realtime_pb2.Alert.Effect.items()
}


class GTFSAlertsError(RuntimeError):
    """Raised when the GTFS-Realtime service alerts feed cannot be fetched or parsed."""


@dataclass(frozen=True, slots=True)
class AlertActivePeriod:
    start: datetime | None
    end: datetime | None

    def is_active_at(self, moment: datetime) -> bool:
        if self.start is not None and moment < self.start:
            return False
        return not (self.end is not None and moment > self.end)

    def to_dict(self) -> dict[str, str | None]:
        return {
            "start": self.start.isoformat() if self.start is not None else None,
            "end": self.end.isoformat() if self.end is not None else None,
        }


@dataclass(frozen=True, slots=True)
class InformedEntitySelector:
    """Single GTFS-Realtime EntitySelector (logical AND across populated fields)."""

    agency_id: str | None = None
    route_id: str | None = None
    route_type: int | None = None
    direction_id: int | None = None
    stop_id: str | None = None
    trip_id: str | None = None

    def matches(
        self,
        *,
        route_id: str | None = None,
        stop_id: str | None = None,
        direction_id: int | None = None,
    ) -> bool:
        if route_id is not None and self.route_id is not None and self.route_id != route_id:
            return False
        if stop_id is not None and self.stop_id is not None and self.stop_id != stop_id:
            return False
        if (
            direction_id is not None
            and self.direction_id is not None
            and self.direction_id != direction_id
        ):
            return False

        # Ensure the selector actually constrains at least one queried dimension.
        if route_id is not None and stop_id is None and direction_id is None:
            return self.route_id is not None
        if stop_id is not None and route_id is None and direction_id is None:
            return self.stop_id is not None
        if direction_id is not None and route_id is None and stop_id is None:
            return self.direction_id is not None

        return (
            (route_id is not None and self.route_id is not None)
            or (stop_id is not None and self.stop_id is not None)
            or (direction_id is not None and self.direction_id is not None)
        )

    def to_dict(self) -> dict[str, str | int]:
        payload: dict[str, str | int] = {}
        if self.agency_id is not None:
            payload["agency_id"] = self.agency_id
        if self.route_id is not None:
            payload["route_id"] = self.route_id
        if self.route_type is not None:
            payload["route_type"] = self.route_type
        if self.direction_id is not None:
            payload["direction_id"] = self.direction_id
        if self.stop_id is not None:
            payload["stop_id"] = self.stop_id
        if self.trip_id is not None:
            payload["trip_id"] = self.trip_id
        return payload


@dataclass(frozen=True, slots=True)
class ServiceAlert:
    alert_id: str
    header: str
    description: str
    url: str | None
    cause: str
    effect: str
    informed_entities: tuple[InformedEntitySelector, ...]
    active_periods: tuple[AlertActivePeriod, ...]

    @property
    def route_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(e.route_id for e in self.informed_entities if e.route_id))

    @property
    def stop_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(e.stop_id for e in self.informed_entities if e.stop_id))

    @property
    def trip_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(e.trip_id for e in self.informed_entities if e.trip_id))

    def is_active_at(self, moment: datetime) -> bool:
        if not self.active_periods:
            return True
        return any(period.is_active_at(moment) for period in self.active_periods)

    def matches_entity(
        self,
        *,
        route_id: str | None = None,
        stop_id: str | None = None,
        direction_id: int | None = None,
    ) -> bool:
        if route_id is None and stop_id is None and direction_id is None:
            return True
        # Logical OR across informed_entity messages per GTFS-RT specification.
        return any(
            selector.matches(
                route_id=route_id,
                stop_id=stop_id,
                direction_id=direction_id,
            )
            for selector in self.informed_entities
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "alert_id": self.alert_id,
            "header": self.header,
            "description": self.description,
            "url": self.url,
            "cause": self.cause,
            "effect": self.effect,
            "route_ids": list(self.route_ids),
            "stop_ids": list(self.stop_ids),
            "trip_ids": list(self.trip_ids),
            "informed_entities": [selector.to_dict() for selector in self.informed_entities],
            "active_periods": [period.to_dict() for period in self.active_periods],
        }


@dataclass(frozen=True, slots=True)
class AlertsSnapshot:
    fetched_at: datetime
    feed_timestamp: datetime | None
    alerts: tuple[ServiceAlert, ...]


def _first_translation(
    translated: gtfs_realtime_pb2.TranslatedString,
    lang: str = "pl",
) -> str:
    for item in translated.translation:
        if item.language.strip().lower() == lang and item.text.strip():
            return item.text.strip()
    for item in translated.translation:
        if item.text.strip():
            return item.text.strip()
    return ""


class ZTMServiceAlerts:
    BASE_URL = "https://www.ztm.poznan.pl"
    RT_ENDPOINT = f"{BASE_URL}/pl/dla-deweloperow/getGtfsRtFile"

    def __init__(self) -> None:
        self._snapshot_lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._snapshot: AlertsSnapshot | None = None

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    # -----------------------------------------------------------------------
    # Fetching & parsing
    # -----------------------------------------------------------------------

    def fetch_feed(self) -> gtfs_realtime_pb2.FeedMessage:
        try:
            response = requests.get(
                self.RT_ENDPOINT,
                params={"file": "feeds.pb"},
                timeout=ini.request_timeout_seconds,
            )
            response.raise_for_status()

            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(response.content)
            return feed
        except Exception as exc:
            raise GTFSAlertsError(f"Unable to fetch GTFS-Realtime service alerts: {exc}") from exc

    def refresh(self) -> AlertsSnapshot:
        """Fetch service alerts and atomically replace the current snapshot."""
        with self._refresh_lock:
            logger.info("Fetching ZTM service alerts")
            feed = self.fetch_feed()
            new_snapshot = self._build_snapshot(feed)

            with self._snapshot_lock:
                self._snapshot = new_snapshot

            logger.info("Service alerts refreshed: %d alerts", len(new_snapshot.alerts))
            return new_snapshot

    def _build_snapshot(self, feed: gtfs_realtime_pb2.FeedMessage) -> AlertsSnapshot:
        feed_timestamp: datetime | None = None
        if feed.HasField("header") and feed.header.HasField("timestamp"):
            feed_timestamp = datetime.fromtimestamp(feed.header.timestamp, tz=POZNAN_TZ)

        alerts: list[ServiceAlert] = []
        for idx, entity in enumerate(feed.entity, start=1):
            if not entity.HasField("alert"):
                continue

            alert = entity.alert
            header = _first_translation(alert.header_text)
            description = _first_translation(alert.description_text)
            url = _first_translation(alert.url) or None

            selectors: list[InformedEntitySelector] = []
            for ie in alert.informed_entity:
                agency_id = ie.agency_id.strip() if ie.agency_id.strip() else None
                route_id = ie.route_id.strip() if ie.route_id.strip() else None
                route_type = ie.route_type if ie.HasField("route_type") else None
                direction_id = ie.direction_id if ie.HasField("direction_id") else None
                stop_id = ie.stop_id.strip() if ie.stop_id.strip() else None
                trip_id = (
                    ie.trip.trip_id.strip()
                    if ie.HasField("trip") and ie.trip.trip_id.strip()
                    else None
                )
                selector = InformedEntitySelector(
                    agency_id=agency_id,
                    route_id=route_id,
                    route_type=route_type,
                    direction_id=direction_id,
                    stop_id=stop_id,
                    trip_id=trip_id,
                )
                if selector not in selectors:
                    selectors.append(selector)

            periods: list[AlertActivePeriod] = []
            for tr in alert.active_period:
                start = (
                    datetime.fromtimestamp(tr.start, tz=POZNAN_TZ)
                    if tr.HasField("start") and tr.start > 0
                    else None
                )
                end = (
                    datetime.fromtimestamp(tr.end, tz=POZNAN_TZ)
                    if tr.HasField("end") and tr.end > 0
                    else None
                )
                periods.append(AlertActivePeriod(start=start, end=end))

            cause = (
                CAUSE_NAMES.get(alert.cause, str(alert.cause))
                if alert.HasField("cause")
                else "UNKNOWN_CAUSE"
            )
            effect = (
                EFFECT_NAMES.get(alert.effect, str(alert.effect))
                if alert.HasField("effect")
                else "UNKNOWN_EFFECT"
            )

            alerts.append(
                ServiceAlert(
                    alert_id=entity.id.strip() or f"alert-{idx}",
                    header=header,
                    description=description,
                    url=url,
                    cause=cause,
                    effect=effect,
                    informed_entities=tuple(selectors),
                    active_periods=tuple(periods),
                )
            )

        return AlertsSnapshot(
            fetched_at=datetime.now(POZNAN_TZ),
            feed_timestamp=feed_timestamp,
            alerts=tuple(alerts),
        )

    # -----------------------------------------------------------------------
    # Querying
    # -----------------------------------------------------------------------

    @property
    def snapshot(self) -> AlertsSnapshot | None:
        with self._snapshot_lock:
            return self._snapshot

    def get_alerts(
        self,
        *,
        route_id: str | None = None,
        stop_id: str | None = None,
        direction_id: int | None = None,
        active_only: bool = True,
        at: datetime | None = None,
    ) -> list[ServiceAlert]:
        snapshot = self.snapshot
        if snapshot is None:
            raise GTFSAlertsError("Service alerts snapshot is not loaded yet")

        moment = at if at is not None else datetime.now(POZNAN_TZ)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=POZNAN_TZ)

        route_filter = route_id.strip() if route_id else None
        stop_filter = stop_id.strip() if stop_id else None

        matched: list[ServiceAlert] = []
        for alert in snapshot.alerts:
            if active_only and not alert.is_active_at(moment):
                continue
            if not alert.matches_entity(
                route_id=route_filter,
                stop_id=stop_filter,
                direction_id=direction_id,
            ):
                continue
            matched.append(alert)

        return matched

    # -----------------------------------------------------------------------
    # Background refresh
    # -----------------------------------------------------------------------

    def start_auto_refresh(self, interval_seconds: int = 60) -> None:
        if interval_seconds < 1:
            raise ValueError("interval_seconds must be positive")

        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Service alerts refresh is already running")

        self._stop_event.clear()

        def _runner() -> None:
            while not self._stop_event.is_set():
                try:
                    self.refresh()
                except Exception:
                    logger.exception("Service alerts refresh failed; keeping previous snapshot")

                self._stop_event.wait(timeout=interval_seconds)

        self._thread = threading.Thread(
            target=_runner,
            name="ztm-service-alerts",
            daemon=True,
        )
        self._thread.start()
        logger.info("Service alerts refresh worker started")

    def stop_auto_refresh(self) -> None:
        self._stop_event.set()

        if self._thread is not None:
            self._thread.join()
            self._thread = None

        logger.info("Service alerts refresh worker stopped")
