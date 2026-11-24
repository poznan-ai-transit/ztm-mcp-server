from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from google.transit import gtfs_realtime_pb2

from services.ztm_service_alerts import (
    POZNAN_TZ,
    GTFSAlertsError,
    ZTMServiceAlerts,
)

BASE_TIME = datetime(2026, 6, 1, 12, 0, tzinfo=POZNAN_TZ)


def make_alerts_feed(
    alerts_spec: list[dict] | None = None,
    *,
    timestamp: datetime = BASE_TIME,
) -> gtfs_realtime_pb2.FeedMessage:
    """Build a GTFS-Realtime FULL_DATASET FeedMessage containing Alert entities."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.incrementality = gtfs_realtime_pb2.FeedHeader.FULL_DATASET
    feed.header.timestamp = int(timestamp.timestamp())

    for idx, spec in enumerate(alerts_spec or [], start=1):
        entity = feed.entity.add()
        entity.id = spec.get("id", f"alert-{idx}")

        alert = entity.alert
        for lang, text in spec.get("header", [("pl", "Objazd linii")]):
            tr = alert.header_text.translation.add()
            tr.language = lang
            tr.text = text

        for lang, text in spec.get("description", []):
            tr = alert.description_text.translation.add()
            tr.language = lang
            tr.text = text

        for lang, text in spec.get("url", []):
            tr = alert.url.translation.add()
            tr.language = lang
            tr.text = text

        if "cause" in spec:
            alert.cause = gtfs_realtime_pb2.Alert.Cause.Value(spec["cause"])
        if "effect" in spec:
            alert.effect = gtfs_realtime_pb2.Alert.Effect.Value(spec["effect"])

        for selector in spec.get("selectors", []):
            ie = alert.informed_entity.add()
            if "agency_id" in selector:
                ie.agency_id = selector["agency_id"]
            if "route_id" in selector:
                ie.route_id = selector["route_id"]
            if "route_type" in selector:
                ie.route_type = selector["route_type"]
            if "direction_id" in selector:
                ie.direction_id = selector["direction_id"]
            if "stop_id" in selector:
                ie.stop_id = selector["stop_id"]
            if "trip_id" in selector:
                ie.trip.trip_id = selector["trip_id"]

        for start_dt, end_dt in spec.get("periods", []):
            period = alert.active_period.add()
            if start_dt is not None:
                period.start = int(start_dt.timestamp())
            if end_dt is not None:
                period.end = int(end_dt.timestamp())

    return feed


@pytest.fixture
def alerts_service() -> ZTMServiceAlerts:
    return ZTMServiceAlerts()


# ---------------------------------------------------------------------------
# Snapshot parsing & translation tests
# ---------------------------------------------------------------------------


def test_build_snapshot_parses_translations_entities_and_enums(alerts_service):
    feed = make_alerts_feed(
        [
            {
                "id": "ztm-101",
                "header": [("en", "Detour"), ("pl", "Objazd na ul. Głogowskiej")],
                "description": [("pl", "Autobusy kierowane są trasą zmienioną.")],
                "url": [("pl", "https://www.ztm.poznan.pl/komunikat/101")],
                "cause": "CONSTRUCTION",
                "effect": "DETOUR",
                "selectors": [
                    {"route_id": "164", "stop_id": "S1"},
                    {"route_id": "164", "stop_id": "S1"},  # duplicate selector deduplicated
                    {"route_id": " 179 ", "stop_id": "S2", "direction_id": 1},
                    {"trip_id": "T1"},
                ],
                "periods": [
                    (BASE_TIME - timedelta(hours=1), BASE_TIME + timedelta(hours=2)),
                ],
            }
        ]
    )

    snapshot = alerts_service._build_snapshot(feed)

    assert snapshot.feed_timestamp == BASE_TIME
    assert len(snapshot.alerts) == 1

    alert = snapshot.alerts[0]
    assert alert.alert_id == "ztm-101"
    assert alert.header == "Objazd na ul. Głogowskiej"
    assert alert.description == "Autobusy kierowane są trasą zmienioną."
    assert alert.url == "https://www.ztm.poznan.pl/komunikat/101"
    assert alert.cause == "CONSTRUCTION"
    assert alert.effect == "DETOUR"
    assert alert.route_ids == ("164", "179")
    assert alert.stop_ids == ("S1", "S2")
    assert alert.trip_ids == ("T1",)
    assert len(alert.informed_entities) == 3
    assert len(alert.active_periods) == 1
    assert alert.to_dict()["route_ids"] == ["164", "179"]


def test_build_snapshot_falls_back_to_first_non_empty_translation(alerts_service):
    feed = make_alerts_feed(
        [
            {
                "id": "ztm-102",
                "header": [("de", "Umleitung"), ("en", "Detour")],
                "description": [("", "Fallback description")],
            }
        ]
    )

    snapshot = alerts_service._build_snapshot(feed)
    alert = snapshot.alerts[0]

    assert alert.header == "Umleitung"
    assert alert.description == "Fallback description"
    assert alert.url is None
    assert alert.cause == "UNKNOWN_CAUSE"
    assert alert.effect == "UNKNOWN_EFFECT"


def test_build_snapshot_ignores_non_alert_entities(alerts_service):
    feed = gtfs_realtime_pb2.FeedMessage()
    entity = feed.entity.add()
    entity.id = "tu-1"
    entity.trip_update.trip.trip_id = "T1"

    snapshot = alerts_service._build_snapshot(feed)
    assert snapshot.alerts == ()


# ---------------------------------------------------------------------------
# Filtering & active period tests
# ---------------------------------------------------------------------------


def test_get_alerts_filters_by_active_period(alerts_service):
    feed = make_alerts_feed(
        [
            {
                "id": "active-now",
                "header": [("pl", "Aktywny")],
                "periods": [(BASE_TIME - timedelta(minutes=30), BASE_TIME + timedelta(minutes=30))],
            },
            {
                "id": "open-ended-end",
                "header": [("pl", "Bez daty końcowej")],
                "periods": [(BASE_TIME - timedelta(days=1), None)],
            },
            {
                "id": "open-ended-start",
                "header": [("pl", "Bez daty początkowej")],
                "periods": [(None, BASE_TIME + timedelta(hours=1))],
            },
            {
                "id": "no-periods",
                "header": [("pl", "Brak zakresu dat")],
                "periods": [],
            },
            {
                "id": "expired",
                "header": [("pl", "Zakończony")],
                "periods": [(BASE_TIME - timedelta(hours=3), BASE_TIME - timedelta(minutes=1))],
            },
            {
                "id": "future",
                "header": [("pl", "Planowany")],
                "periods": [(BASE_TIME + timedelta(minutes=15), BASE_TIME + timedelta(hours=2))],
            },
        ]
    )

    with alerts_service._snapshot_lock:
        alerts_service._snapshot = alerts_service._build_snapshot(feed)

    active = alerts_service.get_alerts(active_only=True, at=BASE_TIME)
    assert [a.alert_id for a in active] == [
        "active-now",
        "open-ended-end",
        "open-ended-start",
        "no-periods",
    ]

    all_alerts = alerts_service.get_alerts(active_only=False, at=BASE_TIME)
    assert [a.alert_id for a in all_alerts] == [
        "active-now",
        "open-ended-end",
        "open-ended-start",
        "no-periods",
        "expired",
        "future",
    ]


def test_informed_entity_and_within_selector_or_across_selectors(alerts_service):
    """GTFS-RT spec: AND within a single informed_entity, OR across informed_entity items."""
    feed = make_alerts_feed(
        [
            {
                "id": "0",
                "header": [("en", "Stop at Elm street is closed")],
                "cause": "CONSTRUCTION",
                "effect": "DETOUR",
                "selectors": [
                    {"route_id": "219", "stop_id": "16130"},
                    {"stop_id": "16230"},
                ],
            }
        ]
    )

    with alerts_service._snapshot_lock:
        alerts_service._snapshot = alerts_service._build_snapshot(feed)

    # Matches selector 1 (route 219 AND stop 16130)
    assert [
        a.alert_id for a in alerts_service.get_alerts(route_id="219", stop_id="16130", at=BASE_TIME)
    ] == ["0"]
    # Route 164 at stop 16130 does NOT match selector 1
    assert alerts_service.get_alerts(route_id="164", stop_id="16130", at=BASE_TIME) == []
    # Matches selector 2 (all routes at stop 16230)
    assert [
        a.alert_id for a in alerts_service.get_alerts(route_id="164", stop_id="16230", at=BASE_TIME)
    ] == ["0"]
    # Single-dimension lookups still find matching alerts
    assert [a.alert_id for a in alerts_service.get_alerts(route_id="219", at=BASE_TIME)] == ["0"]
    assert [a.alert_id for a in alerts_service.get_alerts(stop_id="16130", at=BASE_TIME)] == ["0"]
    assert [a.alert_id for a in alerts_service.get_alerts(stop_id="16230", at=BASE_TIME)] == ["0"]


def test_get_alerts_filters_by_direction_id(alerts_service):
    feed = make_alerts_feed(
        [
            {
                "id": "dir-0-only",
                "header": [("pl", "Objazd kierunek 0")],
                "selectors": [{"route_id": "R1", "direction_id": 0}],
            },
            {
                "id": "both-dirs",
                "header": [("pl", "Objazd oba kierunki")],
                "selectors": [{"route_id": "R1"}],
            },
        ]
    )

    with alerts_service._snapshot_lock:
        alerts_service._snapshot = alerts_service._build_snapshot(feed)

    assert [
        a.alert_id for a in alerts_service.get_alerts(route_id="R1", direction_id=0, at=BASE_TIME)
    ] == [
        "dir-0-only",
        "both-dirs",
    ]
    assert [
        a.alert_id for a in alerts_service.get_alerts(route_id="R1", direction_id=1, at=BASE_TIME)
    ] == [
        "both-dirs",
    ]


def test_get_alerts_returns_empty_before_first_refresh(alerts_service):
    assert alerts_service.snapshot is None
    with pytest.raises(GTFSAlertsError, match="not loaded yet"):
        alerts_service.get_alerts()


# ---------------------------------------------------------------------------
# Refresh & failure resilience tests
# ---------------------------------------------------------------------------


def test_refresh_replaces_snapshot(alerts_service, monkeypatch):
    feed = make_alerts_feed([{"id": "a1", "header": [("pl", "Test")]}])
    monkeypatch.setattr(alerts_service, "fetch_feed", lambda: feed)

    snapshot = alerts_service.refresh()

    assert alerts_service.snapshot is snapshot
    assert len(snapshot.alerts) == 1
    assert snapshot.alerts[0].alert_id == "a1"


def test_refresh_preserves_previous_snapshot_on_failure(alerts_service, monkeypatch):
    feed = make_alerts_feed([{"id": "a1", "header": [("pl", "Test")]}])
    monkeypatch.setattr(alerts_service, "fetch_feed", lambda: feed)

    previous_snapshot = alerts_service.refresh()

    def failing_fetch():
        raise GTFSAlertsError("Network error")

    monkeypatch.setattr(alerts_service, "fetch_feed", failing_fetch)

    with pytest.raises(GTFSAlertsError):
        alerts_service.refresh()

    assert alerts_service.snapshot is previous_snapshot
