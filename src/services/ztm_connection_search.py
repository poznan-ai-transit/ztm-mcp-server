from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from heapq import merge

from services.ztm_static_schedule import (
    PrecomputedData,
    ServiceConnectionTemplate,
    ZTMStaticSchedule,
)


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


@dataclass(eq=False, slots=True)
class _Label:
    """A reachable-stop label retained during the CSA scan (integer-second timestamps)."""

    stop_id: str
    arrival_secs: int
    transfers: int
    last_trip_id: str | None
    can_alight: bool
    previous: _Label | None = None
    service_day: date | None = None
    template: ServiceConnectionTemplate | None = None


# Flat tuple stored in the reusable scan array:
# (dep_rel_secs, arr_rel_secs, service_day, template)
_ScanSegment = tuple[int, int, date, ServiceConnectionTemplate]


class ZTMConnectionSearchService:
    """Search scheduled public-transport connections using GTFS data.

    The service does not own or parse GTFS data. It reads one immutable-by-
    convention ``PrecomputedData`` snapshot from a date-selected
    ``ZTMStaticSchedule`` per search. It never combines records from other feeds.

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
        schedule: ZTMStaticSchedule,
        *,
        minimum_transfer_seconds: int = DEFAULT_MINIMUM_TRANSFER_SECONDS,
        search_horizon_seconds: int = DEFAULT_SEARCH_HORIZON_SECONDS,
    ) -> None:
        if minimum_transfer_seconds < 0:
            raise ValueError("minimum_transfer_seconds cannot be negative")
        if search_horizon_seconds <= 0:
            raise ValueError("search_horizon_seconds must be positive")

        self._schedule = schedule
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

        # Published schedules are immutable by convention; retain this feed's data.
        data: PrecomputedData = self._schedule.data

        if request.from_stop_id == request.to_stop_id:
            return []

        horizon_secs = self._search_horizon_seconds
        scheduled = self._get_scan_segments(
            data,
            request.departure_time,
            horizon_secs,
        )

        if not scheduled:
            return []

        min_transfer = self._minimum_transfer_seconds
        to_stop_id = request.to_stop_id

        # Round-based CSA operating purely on integer seconds relative to
        # request.departure_time (0 == request.departure_time).
        previous_round: dict[str, _Label] = {
            request.from_stop_id: _Label(
                stop_id=request.from_stop_id,
                arrival_secs=0,
                transfers=0,
                last_trip_id=None,
                can_alight=True,
            )
        }

        destination_labels: list[_Label] = []
        scan_cutoff_secs = horizon_secs

        for transfer_round in range(request.max_transfers + 1):
            current_round: dict[str, _Label] = {}

            # Stores the latest position reached on each boarded trip instance.
            onboard: dict[tuple[str, date], _Label] = {}

            for dep_secs, arr_secs, service_day, tpl in scheduled:
                if dep_secs > scan_cutoff_secs:
                    break

                trip_id = tpl.trip_id
                trip_instance = (trip_id, service_day)
                state = onboard.get(trip_instance)

                if state is not None:
                    # Continue travelling on the same vehicle.
                    if state.stop_id != tpl.departure_stop_id:
                        continue

                    if dep_secs < state.arrival_secs:
                        continue

                    previous_label = state
                else:
                    # Board a new trip using a label from the previous round.
                    previous_label = previous_round.get(tpl.departure_stop_id)
                    if previous_label is None:
                        continue

                    if tpl.pickup_type == 1:
                        continue

                    earliest_boarding = (
                        previous_label.arrival_secs + min_transfer
                        if previous_label.last_trip_id is not None
                        else previous_label.arrival_secs
                    )

                    if dep_secs < earliest_boarding:
                        continue

                if arr_secs > scan_cutoff_secs:
                    continue

                can_alight = tpl.drop_off_type != 1
                candidate = _Label(
                    stop_id=tpl.arrival_stop_id,
                    arrival_secs=arr_secs,
                    transfers=transfer_round,
                    last_trip_id=trip_id,
                    can_alight=can_alight,
                    previous=previous_label,
                    service_day=service_day,
                    template=tpl,
                )

                # Remember the passenger's position on this trip,
                # even when alighting is prohibited at this stop.
                onboard[trip_instance] = candidate

                if not can_alight:
                    continue

                existing = current_round.get(candidate.stop_id)
                if existing is None or arr_secs < existing.arrival_secs:
                    current_round[candidate.stop_id] = candidate

            destination_label = current_round.get(to_stop_id)
            if destination_label is not None:
                destination_labels.append(destination_label)
                # Every subsequent round has strictly more transfers, so any
                # non-dominated journey in later rounds must arrive strictly
                # earlier than the best arrival found so far.
                if destination_label.arrival_secs - 1 < scan_cutoff_secs:
                    scan_cutoff_secs = destination_label.arrival_secs - 1

            if not current_round or scan_cutoff_secs < 0:
                break

            previous_round = current_round

        journeys: list[Connection] = []

        for label in destination_labels:
            journey = self._reconstruct_connection(
                data=data,
                origin_stop_id=request.from_stop_id,
                destination_stop_id=to_stop_id,
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

        return journeys[: request.limit]

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

    def _get_scan_segments(
        self,
        data: PrecomputedData,
        departure_time: datetime,
        horizon_secs: int,
    ) -> list[_ScanSegment]:
        """
        Slice each relevant service day's shared template tuple to the search
        window and emit lightweight integer-offset tuples for C-level merging.
        """
        try:
            first_day = departure_time.date() - timedelta(days=data.connection_index_lookback_days)
        except OverflowError:
            first_day = departure_time.date().min

        horizon_end = departure_time + timedelta(seconds=horizon_secs)
        last_day = horizon_end.date()

        daily_lists: list[list[_ScanSegment]] = []
        day = first_day

        while day <= last_day:
            templates = data.connections_by_service_day.get(day)
            if templates:
                day_midnight = datetime(day.year, day.month, day.day)
                # Offset in seconds from request.departure_time to day_midnight
                day_offset_secs = int((day_midnight - departure_time).total_seconds())
                start_secs = -day_offset_secs
                end_secs = horizon_secs - day_offset_secs

                start_idx = bisect_left(
                    templates,
                    start_secs,
                    key=lambda tpl: tpl.departure_secs,
                )
                end_idx = bisect_right(
                    templates,
                    end_secs,
                    lo=start_idx,
                    key=lambda tpl: tpl.departure_secs,
                )

                if start_idx < end_idx:
                    daily_lists.append(
                        [
                            (
                                day_offset_secs + tpl.departure_secs,
                                day_offset_secs + tpl.arrival_secs,
                                day,
                                tpl,
                            )
                            for tpl in templates[start_idx:end_idx]
                        ]
                    )

            day += timedelta(days=1)

        if not daily_lists:
            return []

        if len(daily_lists) == 1:
            return daily_lists[0]

        # C-level tuple comparison on (dep_rel_secs, arr_rel_secs, day, tpl)
        # avoids Python key= callbacks across hundreds of thousands of items.
        return list(merge(*daily_lists))

    def _reconstruct_connection(
        self,
        *,
        data: PrecomputedData,
        origin_stop_id: str,
        destination_stop_id: str,
        destination_label: _Label,
    ) -> Connection | None:
        segments: list[tuple[date, ServiceConnectionTemplate]] = []
        label: _Label | None = destination_label

        while label is not None and label.template is not None and label.service_day is not None:
            segments.append((label.service_day, label.template))
            label = label.previous

        segments.reverse()
        if not segments:
            return None

        # Collapse consecutive adjacent-stop segments on the same trip into
        # one passenger-facing leg.
        grouped: list[list[tuple[date, ServiceConnectionTemplate]]] = []
        for service_day, tpl in segments:
            if (
                grouped
                and grouped[-1][-1][1].trip_id == tpl.trip_id
                and grouped[-1][-1][0] == service_day
                and grouped[-1][-1][1].arrival_stop_id == tpl.departure_stop_id
            ):
                grouped[-1].append((service_day, tpl))
            else:
                grouped.append([(service_day, tpl)])

        legs: list[ConnectionLeg] = []
        for group in grouped:
            first_day, first = group[0]
            last_day, last = group[-1]
            trip = data.trips_by_id.get(first.trip_id)
            route = data.routes_by_id.get(first.route_id)
            from_stop = data.stops_by_id.get(first.departure_stop_id)
            to_stop = data.stops_by_id.get(last.arrival_stop_id)

            if trip is None or from_stop is None or to_stop is None:
                return None

            first_midnight = datetime(first_day.year, first_day.month, first_day.day)
            last_midnight = datetime(last_day.year, last_day.month, last_day.day)

            legs.append(
                ConnectionLeg(
                    trip_id=first.trip_id,
                    route_id=first.route_id,
                    route_short_name=(route.route_short_name if route else first.route_id),
                    trip_headsign=trip.trip_headsign,
                    from_stop_id=first.departure_stop_id,
                    from_stop_name=from_stop.stop_name,
                    departure_time=first_midnight + timedelta(seconds=first.departure_secs),
                    to_stop_id=last.arrival_stop_id,
                    to_stop_name=to_stop.stop_name,
                    arrival_time=last_midnight + timedelta(seconds=last.arrival_secs),
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
