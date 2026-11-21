from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
import copy
from google.transit import gtfs_realtime_pb2

from services.ztm_realtime_delays import (
    POZNAN_TZ,
    GTFSRealtimeError,
    ZTMRealtimeDelays,
)
from services.ztm_static_schedule import ZTMStaticSchedule

SERVICE_DAY = date(2026, 6, 1)


class FakeScheduleRepository:
    """Minimal repository that always returns the supplied test schedule."""

    def __init__(self, schedule):
        self.schedule = schedule

    def for_date(self, service_day: date):
        return self.schedule


@pytest.fixture
def realtime_service(sample_schedule):
    repository = FakeScheduleRepository(sample_schedule)
    return ZTMRealtimeDelays(repository)


@pytest.fixture
def multi_stop_realtime_service(sample_gtfs):
    gtfs = copy.deepcopy(sample_gtfs)
    gtfs["trips"].append(
        {
            "trip_id": "T_NO_DROP",
            "route_id": "R1",
            "service_id": "WKD",
            "trip_headsign": "Dworzec",
            "direction_id": "0",
            "brigade": "",
        }
    )
    gtfs["stop_times"].extend(
        [
            {
                "trip_id": "T_NO_DROP",
                "stop_id": "S1",
                "arrival_time": "05:38:00",
                "departure_time": "05:38:00",
                "stop_sequence": "0",
                "stop_headsign": "Dworzec",
                "pickup_type": "0",
                "drop_off_type": "0",
            },
            {
                "trip_id": "T_NO_DROP",
                "stop_id": "S2",
                "arrival_time": "05:43:00",
                "departure_time": "05:43:00",
                "stop_sequence": "1",
                "stop_headsign": "Dworzec",
                "pickup_type": "0",
                "drop_off_type": "1",
            },
            {
                "trip_id": "T_NO_DROP",
                "stop_id": "S3",
                "arrival_time": "06:30:00",
                "departure_time": "06:30:00",
                "stop_sequence": "2",
                "stop_headsign": "Dworzec",
                "pickup_type": "0",
                "drop_off_type": "0",
            },
        ]
    )
    repository = FakeScheduleRepository(ZTMStaticSchedule.load(gtfs))
    return ZTMRealtimeDelays(repository)


def make_feed(
    *,
    trip_id: str = "T1",
    service_day: date = SERVICE_DAY,
    stop_sequence: int = 0,
    arrival_delay: int | None = None,
    departure_delay: int | None = None,
    stop_relationship: str | None = None,
    trip_relationship: str | None = None,
    vehicle_id: str | None = None,
) -> gtfs_realtime_pb2.FeedMessage:
    """Build a small GTFS-RT feed containing one trip update."""

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.timestamp = int(datetime(2026, 6, 1, 5, 30, tzinfo=POZNAN_TZ).timestamp())

    entity = feed.entity.add()
    entity.id = "vehicle-1"

    trip_update = entity.trip_update
    trip_update.timestamp = feed.header.timestamp
    if vehicle_id is not None:
        trip_update.vehicle.id = vehicle_id

    trip = trip_update.trip
    trip.trip_id = trip_id
    trip.route_id = "R1"
    trip.start_date = service_day.strftime("%Y%m%d")

    if trip_relationship is not None:
        trip.schedule_relationship = gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship.Value(
            trip_relationship
        )

    stop_update = trip_update.stop_time_update.add()
    stop_update.stop_sequence = stop_sequence

    if arrival_delay is not None:
        stop_update.arrival.delay = arrival_delay

    if departure_delay is not None:
        stop_update.departure.delay = departure_delay

    if stop_relationship is not None:
        stop_update.schedule_relationship = (
            gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.ScheduleRelationship.Value(
                stop_relationship
            )
        )

    return feed


def make_vehicle_positions_feed(
    vehicles: list[tuple[str, str | None, str]],
) -> gtfs_realtime_pb2.FeedMessage:
    """Build a GTFS-RT VehiclePosition feed from ``(trip_id, start_date, vehicle_id)`` tuples."""

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.timestamp = int(datetime(2026, 6, 1, 5, 30, tzinfo=POZNAN_TZ).timestamp())

    for idx, (trip_id, start_date, vehicle_id) in enumerate(vehicles):
        entity = feed.entity.add()
        entity.id = f"vp-{idx}"
        vp = entity.vehicle
        vp.trip.trip_id = trip_id
        if start_date:
            vp.trip.start_date = start_date
        vp.vehicle.id = vehicle_id

    return feed


# ---------------------------------------------------------------------------
# Prediction tests
# ---------------------------------------------------------------------------


def test_predict_event_using_arrival_delay(realtime_service):
    """A negative GTFS-RT delay should predict an earlier arrival."""

    realtime_stop = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate()
    realtime_stop.arrival.delay = -824

    result = realtime_service._predict_event(
        realtime_stop,
        "arrival",
        5 * 3600 + 38 * 60,
        SERVICE_DAY,
    )

    assert result is not None

    predicted, delay = result

    scheduled = datetime(2026, 6, 1, 5, 38, tzinfo=POZNAN_TZ)

    assert delay == -824
    assert predicted == scheduled - timedelta(seconds=824)


def test_predict_event_using_absolute_timestamp(realtime_service):
    """An absolute GTFS-RT event.time should take precedence."""

    realtime_stop = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate()

    scheduled = datetime(2026, 6, 1, 5, 38, tzinfo=POZNAN_TZ)

    predicted_timestamp = int(scheduled.timestamp() + 91)

    realtime_stop.arrival.time = predicted_timestamp

    result = realtime_service._predict_event(
        realtime_stop,
        "arrival",
        5 * 3600 + 38 * 60,
        SERVICE_DAY,
    )

    assert result is not None

    predicted, delay = result

    assert delay == 91
    assert predicted == datetime.fromtimestamp(
        predicted_timestamp,
        tz=POZNAN_TZ,
    )


def test_predict_event_returns_none_when_event_missing(realtime_service):
    """No arrival or departure event should produce no prediction."""

    realtime_stop = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate()

    result = realtime_service._predict_event(
        realtime_stop,
        "arrival",
        5 * 3600 + 38 * 60,
        SERVICE_DAY,
    )

    assert result is None


# ---------------------------------------------------------------------------
# Service-day and midnight tests
# ---------------------------------------------------------------------------


def test_scheduled_datetime_handles_time_after_midnight(realtime_service):
    """GTFS 25:30:00 belongs to the previous service day."""

    result = realtime_service._scheduled_datetime(
        25 * 3600 + 30 * 60,
        SERVICE_DAY,
    )

    assert result.date() == date(2026, 6, 2)
    assert result.hour == 1
    assert result.minute == 30
    assert result.tzinfo == POZNAN_TZ


def test_realtime_prediction_for_trip_after_midnight(realtime_service):
    """Realtime prediction for T2 must retain its June 1 service day."""

    feed = make_feed(
        trip_id="T2",
        arrival_delay=120,
    )

    snapshot = realtime_service._build_snapshot(feed)

    trip = snapshot.trips[("T2", SERVICE_DAY)]
    stop = trip.stop_updates[0]

    assert trip.service_day == SERVICE_DAY
    assert stop.scheduled_arrival == "25:30:00"
    assert stop.arrival_delay == 120

    assert stop.predicted_arrival is not None
    assert stop.predicted_arrival.date() == date(2026, 6, 2)
    assert stop.predicted_arrival.hour == 1
    assert stop.predicted_arrival.minute == 32


# ---------------------------------------------------------------------------
# Snapshot construction tests
# ---------------------------------------------------------------------------


def test_build_snapshot_with_arrival_only_update(realtime_service):
    """
    When ZTM provides only arrival.delay, departure should be estimated
    using the same delay.
    """

    feed = make_feed(arrival_delay=106)

    snapshot = realtime_service._build_snapshot(feed)

    trip = snapshot.trips[("T1", SERVICE_DAY)]
    stop = trip.stop_updates[0]

    assert stop.arrival_delay == 106
    assert stop.departure_delay == 106

    assert stop.departure_delay_estimated is True

    assert stop.predicted_arrival is not None
    assert stop.predicted_departure is not None

    assert stop.predicted_arrival == datetime(2026, 6, 1, 5, 39, 46, tzinfo=POZNAN_TZ)

    # T1 has the same scheduled arrival and departure at S1.
    assert stop.predicted_departure == datetime(2026, 6, 1, 5, 39, 46, tzinfo=POZNAN_TZ)


def test_explicit_departure_delay_takes_precedence(realtime_service):
    """An explicit departure delay must not be replaced by arrival delay."""

    feed = make_feed(
        arrival_delay=60,
        departure_delay=120,
    )

    snapshot = realtime_service._build_snapshot(feed)

    stop = snapshot.trips[("T1", SERVICE_DAY)].stop_updates[0]

    assert stop.arrival_delay == 60
    assert stop.departure_delay == 120

    assert stop.departure_delay_estimated is False

    assert stop.predicted_arrival == datetime(2026, 6, 1, 5, 39, tzinfo=POZNAN_TZ)

    assert stop.predicted_departure == datetime(2026, 6, 1, 5, 40, tzinfo=POZNAN_TZ)


def test_departure_only_update_is_not_estimated(realtime_service):
    """A departure-only event should remain an explicit prediction."""

    feed = make_feed(departure_delay=75)

    snapshot = realtime_service._build_snapshot(feed)

    stop = snapshot.trips[("T1", SERVICE_DAY)].stop_updates[0]

    assert stop.arrival_delay is None
    assert stop.departure_delay == 75
    assert stop.departure_delay_estimated is False

    assert stop.predicted_arrival is None
    assert stop.predicted_departure == datetime(2026, 6, 1, 5, 39, 15, tzinfo=POZNAN_TZ)


def test_skipped_stop_update_is_retained(realtime_service):
    """Relationship-only SKIPPED updates should not be discarded."""

    feed = make_feed(
        stop_relationship="SKIPPED",
    )

    snapshot = realtime_service._build_snapshot(feed)

    trip = snapshot.trips[("T1", SERVICE_DAY)]

    assert len(trip.stop_updates) == 1

    stop = trip.stop_updates[0]

    assert stop.schedule_relationship == "SKIPPED"
    assert stop.predicted_arrival is None
    assert stop.predicted_departure is None


def test_scheduled_relationship_without_prediction_is_ignored(
    realtime_service,
):
    """A trip with no meaningful updates should not enter the snapshot."""

    feed = make_feed()

    snapshot = realtime_service._build_snapshot(feed)

    assert ("T1", SERVICE_DAY) not in snapshot.trips
    assert len(snapshot.trips) == 0


def test_cancelled_trip_without_stop_updates_is_retained(
    realtime_service,
):
    """A cancelled trip may be represented without stop-time updates."""

    feed = gtfs_realtime_pb2.FeedMessage()

    entity = feed.entity.add()
    entity.id = "cancelled-vehicle"

    trip_update = entity.trip_update
    trip_update.trip.trip_id = "T1"
    trip_update.trip.route_id = "R1"
    trip_update.trip.start_date = SERVICE_DAY.strftime("%Y%m%d")
    trip_update.trip.schedule_relationship = (
        gtfs_realtime_pb2.TripDescriptor.ScheduleRelationship.Value("CANCELED")
    )

    snapshot = realtime_service._build_snapshot(feed)

    trip = snapshot.trips[("T1", SERVICE_DAY)]

    assert trip.trip_schedule_relationship == "CANCELED"
    assert trip.stop_updates == ()


def test_unknown_trip_is_ignored(realtime_service):
    """A trip not present in the static schedule cannot be joined."""

    feed = make_feed(
        trip_id="UNKNOWN",
        arrival_delay=60,
    )

    snapshot = realtime_service._build_snapshot(feed)

    assert snapshot.trips == {}


# ---------------------------------------------------------------------------
# Vehicle position mapping tests
# ---------------------------------------------------------------------------


def test_get_vehicle_id_from_vehicle_positions_without_delays(realtime_service):
    """Vehicle positions should map trips to vehicle_id even when no trip updates exist."""

    empty_tu_feed = gtfs_realtime_pb2.FeedMessage()
    vp_feed = make_vehicle_positions_feed(
        [
            ("T1", SERVICE_DAY.strftime("%Y%m%d"), "3011"),
            ("3_9999", None, "3022"),
        ]
    )

    snapshot = realtime_service._build_snapshot(empty_tu_feed, vp_feed)
    with realtime_service._snapshot_lock:
        realtime_service._snapshot = snapshot

    assert realtime_service.get_trip_updates("T1", SERVICE_DAY) is None
    assert realtime_service.get_vehicle_id("T1", SERVICE_DAY) == "3011"
    # Base trip_id before '^' suffix resolves when start_date was omitted
    assert realtime_service.get_vehicle_id("3_9999^N+", SERVICE_DAY) == "3022"
    assert realtime_service.get_vehicle_id("T3", SERVICE_DAY) is None


# ---------------------------------------------------------------------------
# Delay propagation tests
# ---------------------------------------------------------------------------


def test_delay_propagates_to_subsequent_stops(multi_stop_realtime_service):
    """A delay at an earlier stop sequence should propagate forward to omitted stops."""

    # T_NO_DROP has 3 stops: seq 0 (S1 05:38), seq 1 (S2 05:43), seq 2 (S3 06:30)
    feed = make_feed(
        trip_id="T_NO_DROP",
        stop_sequence=0,
        arrival_delay=180,
    )

    snapshot = multi_stop_realtime_service._build_snapshot(feed)
    with multi_stop_realtime_service._snapshot_lock:
        multi_stop_realtime_service._snapshot = snapshot

    seq0 = multi_stop_realtime_service.get_stop_update("T_NO_DROP", 0, SERVICE_DAY)
    assert seq0 is not None
    assert seq0.departure_delay == 180

    seq1 = multi_stop_realtime_service.get_stop_update("T_NO_DROP", 1, SERVICE_DAY)
    assert seq1 is not None
    assert seq1.stop_id == "S2"
    assert seq1.arrival_delay == 180
    assert seq1.departure_delay == 180
    assert seq1.departure_delay_estimated is True
    assert seq1.predicted_arrival == datetime(2026, 6, 1, 5, 46, tzinfo=POZNAN_TZ)
    assert seq1.predicted_departure == datetime(2026, 6, 1, 5, 46, tzinfo=POZNAN_TZ)

    seq2 = multi_stop_realtime_service.get_stop_update("T_NO_DROP", 2, SERVICE_DAY)
    assert seq2 is not None
    assert seq2.stop_id == "S3"
    assert seq2.departure_delay == 180
    assert seq2.predicted_departure == datetime(2026, 6, 1, 6, 33, tzinfo=POZNAN_TZ)


def test_no_data_clears_downstream_delay_propagation(multi_stop_realtime_service):
    """A NO_DATA stop update should stop propagating earlier delays downstream."""

    feed = gtfs_realtime_pb2.FeedMessage()
    entity = feed.entity.add()
    entity.id = "vehicle-1"

    tu = entity.trip_update
    tu.trip.trip_id = "T_NO_DROP"
    tu.trip.route_id = "R1"
    tu.trip.start_date = SERVICE_DAY.strftime("%Y%m%d")

    stu0 = tu.stop_time_update.add()
    stu0.stop_sequence = 0
    stu0.arrival.delay = 240

    stu1 = tu.stop_time_update.add()
    stu1.stop_sequence = 1
    stu1.schedule_relationship = (
        gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.ScheduleRelationship.Value("NO_DATA")
    )

    snapshot = multi_stop_realtime_service._build_snapshot(feed)
    with multi_stop_realtime_service._snapshot_lock:
        multi_stop_realtime_service._snapshot = snapshot

    # Sequence 2 follows sequence 1 (NO_DATA) -> should not inherit sequence 0's delay
    assert multi_stop_realtime_service.get_stop_update("T_NO_DROP", 2, SERVICE_DAY) is None


# ---------------------------------------------------------------------------
# Snapshot access tests
# ---------------------------------------------------------------------------


def test_snapshot_access_methods(realtime_service):
    feed = make_feed(arrival_delay=60)

    snapshot = realtime_service._build_snapshot(feed)

    with realtime_service._snapshot_lock:
        realtime_service._snapshot = snapshot

    trip = realtime_service.get_trip_updates("T1", SERVICE_DAY)

    assert trip is not None
    assert trip.trip_id == "T1"

    stop = realtime_service.get_stop_update(
        "T1",
        0,
        SERVICE_DAY,
    )

    assert stop is not None
    assert stop.stop_id == "S1"

    assert len(realtime_service.get_all_trip_updates()) == 1
    assert len(realtime_service.get_delays_for_stop("S1")) == 1
    assert len(realtime_service.get_delays_for_route("R1")) == 1

    assert (
        realtime_service.get_trip_updates(
            "T1",
            date(2026, 6, 2),
        )
        is None
    )


def test_snapshot_is_immutable(realtime_service):
    """The snapshot's trip mapping should not be mutable."""

    snapshot = realtime_service._build_snapshot(make_feed(arrival_delay=60))

    with pytest.raises(TypeError):
        snapshot.trips[("OTHER", SERVICE_DAY)] = None


# ---------------------------------------------------------------------------
# Refresh tests
# ---------------------------------------------------------------------------


def test_refresh_replaces_snapshot(realtime_service, monkeypatch):
    feed = make_feed(arrival_delay=60)
    vp_feed = make_vehicle_positions_feed([("T1", SERVICE_DAY.strftime("%Y%m%d"), "3011")])

    monkeypatch.setattr(
        realtime_service,
        "fetch_feed",
        lambda: feed,
    )
    monkeypatch.setattr(
        realtime_service,
        "fetch_vehicle_positions",
        lambda: vp_feed,
    )

    snapshot = realtime_service.refresh()

    assert realtime_service.snapshot is snapshot
    assert ("T1", SERVICE_DAY) in snapshot.trips
    assert realtime_service.get_vehicle_id("T1", SERVICE_DAY) == "3011"


def test_refresh_preserves_previous_snapshot_on_failure(
    realtime_service,
    monkeypatch,
):
    """A failed refresh must not discard the last good snapshot."""

    feed = make_feed(arrival_delay=60)
    vp_feed = make_vehicle_positions_feed([])

    monkeypatch.setattr(
        realtime_service,
        "fetch_feed",
        lambda: feed,
    )
    monkeypatch.setattr(
        realtime_service,
        "fetch_vehicle_positions",
        lambda: vp_feed,
    )

    previous_snapshot = realtime_service.refresh()

    def failing_fetch():
        raise GTFSRealtimeError("Network unavailable")

    monkeypatch.setattr(
        realtime_service,
        "fetch_feed",
        failing_fetch,
    )

    with pytest.raises(GTFSRealtimeError):
        realtime_service.refresh()

    assert realtime_service.snapshot is previous_snapshot