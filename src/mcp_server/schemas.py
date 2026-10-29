"""Static GTFS response contracts exposed by the MCP tools.

TypedDict keeps responses as ordinary dictionaries while giving FastMCP concrete
JSON schemas. All fields are required; only explicitly nullable fields allow null.
"""

from typing import Annotated, Literal

from pydantic import Field
from typing_extensions import TypedDict

StopId = Annotated[
    str,
    Field(
        description=(
            "Exact GTFS stop_id returned by search_stops. Pass unchanged as a JSON string, "
            "preserving leading zeros. Do not pass a stop name, stop_code, or stop_sequence. "
            "Reuse a previously resolved ID when applicable; never invent one. "
            "Resolve ambiguous search matches with the user before selecting an ID."
        ),
    ),
]


class Stop(TypedDict):
    """A stop from the loaded static GTFS dataset."""

    stop_id: StopId
    stop_code: str
    stop_name: str
    stop_lat: float
    stop_lon: float
    zone_id: str


class StopMatch(Stop):
    """A stop search result with its similarity score."""

    score: float


class Route(TypedDict):
    """Route metadata returned when listing routes serving a stop."""

    route_id: str
    agency_id: str
    route_short_name: str
    route_long_name: str
    route_type: int


class RouteSearchResult(TypedDict):
    """Route search result with ordered stop names for each direction.

    Direction keys become strings when serialized as JSON object keys.
    """

    route_id: str
    route_short_name: str
    route_long_name: str
    route_type: int
    directions: dict[int, list[str]]
    score: float


class ScheduledDeparture(TypedDict):
    """One scheduled departure; no live prediction or delay is included."""

    departure_time: Annotated[
        str, Field(description="GTFS service-day time HH:MM:SS; hours may exceed 23.")
    ]
    departure_datetime: Annotated[
        str,
        Field(
            description=(
                "ISO Europe/Warsaw local datetime on the actual calendar day, "
                "without a UTC offset; not UTC."
            )
        ),
    ]
    trip_id: str
    route_id: str
    route_short_name: str
    trip_headsign: str
    direction_id: int
    stop_sequence: int


class DeparturesResponse(TypedDict):
    """Scheduled departures and the query context"""

    stop_id: StopId
    stop_name: str
    route_id: str
    route_short_name: str
    date: Annotated[str, Field(description="GTFS service date in YYYY-MM-DD format.")]
    time: Annotated[
        str,
        Field(description="Inclusive Europe/Warsaw start time, HH:MM from 00:00 through 23:59."),
    ]
    direction_id: Literal[0, 1] | None
    realtime: Literal[False]
    departures: list[ScheduledDeparture]
