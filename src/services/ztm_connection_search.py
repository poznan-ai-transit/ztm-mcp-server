from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING
from time import perf_counter

from services.ztm_static_schedule import PrecomputedData, ZTMStaticSchedule

if TYPE_CHECKING:
    from services.ztm_static_schedule import Route, Stop, StopTime, Trip


@dataclass(frozen=True, slots=True)
class ConnectionSearchRequest:
    """A request to find journeys between two GTFS stops.

    ``departure_time`` is expected to be a naive datetime in the feed's
    local timezone. GTFS service times are interpreted in that same timezone.
    """

    from_stop_id: str
    to_stop_id: str
    departure_time: datetime
    max_transfers: int = 2
    limit: int = 5


@dataclass(frozen=True, slots=True)
class ConnectionLeg:
    """One continuous ride on a single GTFS trip."""

    trip_id: str
    route_id: str
    route_short_name: str
    trip_headsign: str
    from_stop_id: str
    from_stop_name: str
    departure_time: datetime
    to_stop_id: str
    to_stop_name: str
    arrival_time: datetime


@dataclass(frozen=True, slots=True)
class Connection:
    """A complete journey, potentially consisting of multiple trip legs."""

    from_stop_id: str
    from_stop_name: str
    to_stop_id: str
    to_stop_name: str
    departure_time: datetime
    arrival_time: datetime
    duration_seconds: int
    transfers: int
    legs: tuple[ConnectionLeg, ...]


@dataclass(frozen=True, slots=True)
class _ScheduledConnection:
    trip_id: str
    route_id: str
    service_id: str
    departure_stop_id: str
    arrival_stop_id: str
    departure_time: datetime
    arrival_time: datetime
    departure_sequence: int
    arrival_sequence: int
    pickup_type: int
    drop_off_type: int


@dataclass(eq=False, slots=True)
class _Label:
    """A reachable-stop label retained during the CSA scan."""

    stop_id: str
    arrival_time: datetime
    transfers: int
    last_trip_id: str | None
    can_alight: bool
    previous: _Label | None = None
    connection: _ScheduledConnection | None = None


class ZTMConnectionSearchService:
    """Search scheduled public-transport connections using GTFS data.

    The service does not own or parse GTFS data. It reads one immutable-by-
    convention ``PrecomputedData`` snapshot from ``ZTMStaticSchedule`` per
    search, so a concurrent daily feed reload cannot mix old and new indexes.

    The first implementation supports transfers at the same stop_id only.
    ``pickup_type`` / ``drop_off_type`` value 1 is treated as prohibited;
    values 2 and 3 are retained because they represent conditional boarding
    or alighting in GTFS rather than an absolute prohibition.
    """

    DEFAULT_MAX_TRANSFERS = 2
    DEFAULT_LIMIT = 5
    DEFAULT_MINIMUM_TRANSFER_SECONDS = 0
    DEFAULT_SEARCH_HORIZON_SECONDS = 24 * 60 * 60

    def __init__(
        self,
        schedule: ZTMStaticSchedule | None = None,
        *,
        minimum_transfer_seconds: int = DEFAULT_MINIMUM_TRANSFER_SECONDS,
        search_horizon_seconds: int = DEFAULT_SEARCH_HORIZON_SECONDS,
    ) -> None:
        if minimum_transfer_seconds < 0:
            raise ValueError("minimum_transfer_seconds cannot be negative")
        if search_horizon_seconds <= 0:
            raise ValueError("search_horizon_seconds must be positive")

        self._schedule = schedule or ZTMStaticSchedule.instance()
        self._minimum_transfer_seconds = minimum_transfer_seconds
        self._search_horizon_seconds = search_horizon_seconds

    def search_connections(
        self,
        request: ConnectionSearchRequest,
    ) -> list[Connection]:
        """Return up to ``request.limit`` Pareto-optimal journey alternatives.

        Results are ordered by earliest arrival, then fewest transfers,
        then earliest departure. A journey is considered dominated when
        another journey arrives no later and uses no more transfers.
        """
        self._validate_request(request)

        # Capture once: ZTMStaticSchedule.load() swaps this reference only
        # after a complete new PrecomputedData object has been built.
        data: PrecomputedData = self._schedule.data

        if request.from_stop_id == request.to_stop_id:
            return []

        origin = data.stops_by_id.get(request.from_stop_id)
        destination = data.stops_by_id.get(request.to_stop_id)
        if origin is None:
            raise ValueError(f"Unknown origin stop_id: {request.from_stop_id}")
        if destination is None:
            raise ValueError(f"Unknown destination stop_id: {request.to_stop_id}")

        horizon_end = request.departure_time + timedelta(
            seconds=self._search_horizon_seconds
        )

        started = perf_counter()

        scheduled = self._build_connections(
            data=data,
            departure_time=request.departure_time,
            horizon_end=horizon_end,
        )

        built_at = perf_counter()
        print(
            f"Built {len(scheduled)} connections in {built_at - started:.3f}s")
        
        if not scheduled:
            return []

        scan_started = perf_counter()

        scanned_connections = 0
        connections_with_labels = 0
        labels_checked = 0
        candidates_created = 0
        max_labels_at_stop = 0

        
        # Round-based CSA:
        # round 0 = no transfers
        # round 1 = at most one transfer
        # round 2 = at most two transfers, etc.

        previous_round: dict[str, _Label] = {
            request.from_stop_id: _Label(
                stop_id=request.from_stop_id,
                arrival_time=request.departure_time,
                transfers=0,
                last_trip_id=None,
                can_alight=True,
            )
        }

        destination_labels: list[_Label] = []

        scan_started = perf_counter()
        total_scanned = 0
        total_boardings = 0

        for transfer_round in range(request.max_transfers + 1):
            current_round: dict[str, _Label] = {}

            # Stores the latest position reached on each boarded trip.
            onboard: dict[str, _Label] = {}

            for connection in scheduled:
                if connection.departure_time > horizon_end:
                    break

                state = onboard.get(connection.trip_id)

                if state is not None:
                    # Continue travelling on the same vehicle.
                    if state.stop_id != connection.departure_stop_id:
                        continue

                    if connection.departure_time < state.arrival_time:
                        continue

                    previous_label = state

                else:
                    # Board a new trip using a label from the previous round.
                    previous_label = previous_round.get(
                        connection.departure_stop_id
                    )

                    if previous_label is None:
                        continue

                    if connection.pickup_type == 1:
                        continue

                    transfer_seconds = (
                        self._minimum_transfer_seconds
                        if previous_label.last_trip_id is not None
                        else 0
                    )

                    earliest_boarding = (
                        previous_label.arrival_time
                        + timedelta(seconds=transfer_seconds)
                    )

                    if connection.departure_time < earliest_boarding:
                        continue

                    total_boardings += 1

                total_scanned += 1

                if connection.arrival_time > horizon_end:
                    continue

                candidate = _Label(
                    stop_id=connection.arrival_stop_id,
                    arrival_time=connection.arrival_time,
                    transfers=transfer_round,
                    last_trip_id=connection.trip_id,
                    can_alight=(connection.drop_off_type != 1),
                    previous=previous_label,
                    connection=connection,
                )

                # Remember the passenger's position on this trip,
                # even when alighting is prohibited at this stop.
                onboard[connection.trip_id] = candidate

                if not candidate.can_alight:
                    continue

                existing = current_round.get(candidate.stop_id)

                # For the same transfer round, an earlier arrival
                # dominates a later arrival at the same stop.
                if (
                    existing is None
                    or candidate.arrival_time < existing.arrival_time
                ):
                    current_round[candidate.stop_id] = candidate

            destination_label = current_round.get(request.to_stop_id)

            if destination_label is not None:
                destination_labels.append(destination_label)

            if not current_round:
                break

            previous_round = current_round

        scan_finished = perf_counter()

        journeys: list[Connection] = []

        for label in destination_labels:
            journey = self._reconstruct_connection(
                data=data,
                origin_stop_id=request.from_stop_id,
                destination_stop_id=request.to_stop_id,
                destination_label=label,
            )

            if journey is not None:
                journeys.append(journey)

        journeys = self._pareto_filter_journeys(journeys)

        journeys.sort(
            key=lambda item: (
                item.arrival_time,
                item.transfers,
                item.departure_time,
            )
        )

        finished_at = perf_counter()

        print(
            f"Round-based CSA: {scan_finished - scan_started:.3f}s"
        )
        print(f"Connections processed: {total_scanned:,}")
        print(f"New trip boardings: {total_boardings:,}")
        print(f"Rounds: {transfer_round + 1}")
        print(
            f"Reconstruction and filtering: "
            f"{finished_at - scan_finished:.3f}s"
        )
        print(f"Total search: {finished_at - started:.3f}s")

        return journeys[:request.limit]


    def find_next_connections(
        self,
        from_stop_id: str,
        to_stop_id: str,
        departure_time: datetime,
        limit: int = DEFAULT_LIMIT,
        max_transfers: int = DEFAULT_MAX_TRANSFERS,
    ) -> list[Connection]:
        """Convenience wrapper around :meth:`search_connections`."""
        return self.search_connections(
            ConnectionSearchRequest(
                from_stop_id=from_stop_id,
                to_stop_id=to_stop_id,
                departure_time=departure_time,
                max_transfers=max_transfers,
                limit=limit,
            )
        )

    @staticmethod
    def _validate_request(request: ConnectionSearchRequest) -> None:
        if not request.from_stop_id.strip():
            raise ValueError("from_stop_id cannot be empty")
        if not request.to_stop_id.strip():
            raise ValueError("to_stop_id cannot be empty")
        if not isinstance(request.departure_time, datetime):
            raise TypeError("departure_time must be a datetime")
        if request.departure_time.tzinfo is not None:
            raise ValueError(
                "departure_time must be a naive local datetime; "
                "GTFS feed timezone handling belongs at the API boundary"
            )
        if request.max_transfers < 0:
            raise ValueError("max_transfers cannot be negative")
        if request.limit < 1:
            raise ValueError("limit must be at least 1")

    def _build_connections(
        self,
        *,
        data: PrecomputedData,
        departure_time: datetime,
        horizon_end: datetime,
    ) -> list[_ScheduledConnection]:
        """Build adjacent-stop connections for relevant service days.

        Previous service day is required for GTFS times such as 25:30:00
        that occur after midnight on the following calendar date.
        """
        first_day = departure_time.date() - timedelta(days=1)
        last_day = horizon_end.date()
        connections: list[_ScheduledConnection] = []

        service_day = first_day
        while service_day <= last_day:
            active_services = {
                service_id
                for service_id, service in data.services.items()
                if service.runs_on(service_day)
            }

            if active_services:
                midnight = datetime.combine(service_day, time.min)
                for trip_id, stop_times in data.stop_times_by_trip.items():
                    trip = data.trips_by_id.get(trip_id)
                    if trip is None or trip.service_id not in active_services:
                        continue

                    for current, following in zip(stop_times, stop_times[1:]):
                        departure = midnight + timedelta(
                            seconds=current.departure_secs
                        )
                        arrival = midnight + timedelta(
                            seconds=following.arrival_secs
                        )

                        if departure < departure_time or departure > horizon_end:
                            continue
                        if arrival < departure or arrival > horizon_end:
                            continue

                        connections.append(
                            _ScheduledConnection(
                                trip_id=trip_id,
                                route_id=trip.route_id,
                                service_id=trip.service_id,
                                departure_stop_id=current.stop_id,
                                arrival_stop_id=following.stop_id,
                                departure_time=departure,
                                arrival_time=arrival,
                                departure_sequence=current.stop_sequence,
                                arrival_sequence=following.stop_sequence,
                                pickup_type=current.pickup_type,
                                drop_off_type=following.drop_off_type,
                            )
                        )

            service_day += timedelta(days=1)

        connections.sort(
            key=lambda item: (
                item.departure_time,
                item.arrival_time,
                item.trip_id,
                item.departure_sequence,
            )
        )
        return connections

    @staticmethod
    def _add_nondominated_label(
        labels_by_stop: dict[str, list[_Label]],
        candidate: _Label,
    ) -> None:
        """Keep non-dominated labels for a stop and last-trip state.

        Labels with different last trips are kept separately because being
        aboard a particular trip permits continuation without a transfer.
        """
        labels = labels_by_stop.setdefault(candidate.stop_id, [])
        comparable = [
            existing
            for existing in labels
            if existing.last_trip_id == candidate.last_trip_id
            and existing.can_alight == candidate.can_alight
        ]

        if any(
            existing.arrival_time <= candidate.arrival_time
            and existing.transfers <= candidate.transfers
            for existing in comparable
        ):
            return

        labels[:] = [
            existing
            for existing in labels
            if not (
                existing.last_trip_id == candidate.last_trip_id
                and existing.can_alight == candidate.can_alight
                and candidate.arrival_time <= existing.arrival_time
                and candidate.transfers <= existing.transfers
            )
        ]
        labels.append(candidate)

    def _reconstruct_connection(
        self,
        *,
        data: PrecomputedData,
        origin_stop_id: str,
        destination_stop_id: str,
        destination_label: _Label,
    ) -> Connection | None:
        segments: list[_ScheduledConnection] = []
        label: _Label | None = destination_label

        while label is not None and label.connection is not None:
            segments.append(label.connection)
            label = label.previous

        segments.reverse()
        if not segments:
            return None

        # Collapse consecutive adjacent-stop segments on the same trip into
        # one passenger-facing leg.
        grouped: list[list[_ScheduledConnection]] = []
        for segment in segments:
            if (
                grouped
                and grouped[-1][-1].trip_id == segment.trip_id
                and grouped[-1][-1].arrival_stop_id == segment.departure_stop_id
            ):
                grouped[-1].append(segment)
            else:
                grouped.append([segment])

        legs: list[ConnectionLeg] = []
        for group in grouped:
            first = group[0]
            last = group[-1]
            trip = data.trips_by_id.get(first.trip_id)
            route = data.routes_by_id.get(first.route_id)
            from_stop = data.stops_by_id.get(first.departure_stop_id)
            to_stop = data.stops_by_id.get(last.arrival_stop_id)

            if trip is None or from_stop is None or to_stop is None:
                return None

            legs.append(
                ConnectionLeg(
                    trip_id=first.trip_id,
                    route_id=first.route_id,
                    route_short_name=(
                        route.route_short_name if route else first.route_id
                    ),
                    trip_headsign=trip.trip_headsign,
                    from_stop_id=first.departure_stop_id,
                    from_stop_name=from_stop.stop_name,
                    departure_time=first.departure_time,
                    to_stop_id=last.arrival_stop_id,
                    to_stop_name=to_stop.stop_name,
                    arrival_time=last.arrival_time,
                )
            )

        origin = data.stops_by_id.get(origin_stop_id)
        destination = data.stops_by_id.get(destination_stop_id)
        if origin is None or destination is None:
            return None

        first_departure = legs[0].departure_time
        final_arrival = legs[-1].arrival_time

        return Connection(
            from_stop_id=origin_stop_id,
            from_stop_name=origin.stop_name,
            to_stop_id=destination_stop_id,
            to_stop_name=destination.stop_name,
            departure_time=first_departure,
            arrival_time=final_arrival,
            duration_seconds=int((final_arrival - first_departure).total_seconds()),
            transfers=max(0, len(legs) - 1),
            legs=tuple(legs),
        )

    @staticmethod
    def _pareto_filter_journeys(
        journeys: list[Connection],
    ) -> list[Connection]:
        """Remove journeys dominated by an earlier-arriving journey with
        no more transfers. Keep distinct journeys with equal metrics.
        """
        result: list[Connection] = []
        for candidate in sorted(
            journeys,
            key=lambda item: (
                item.arrival_time,
                item.transfers,
                item.departure_time,
            ),
        ):
            dominated = any(
                existing.arrival_time <= candidate.arrival_time
                and existing.transfers <= candidate.transfers
                and (
                    existing.arrival_time < candidate.arrival_time
                    or existing.transfers < candidate.transfers
                )
                for existing in result
            )
            if not dominated:
                result.append(candidate)
        return result
