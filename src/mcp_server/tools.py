# tools.py
from __future__ import annotations
from dataclasses import asdict
from datetime import date as calendar_date
import re
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from logging_config import logger
from services.ztm_static_schedule import ZTMStaticSchedule


mcp_tools: FastMCP = FastMCP("ztm-poznan-tools")


@mcp_tools.tool()
def echo(text: str) -> str:
    """Test tool — returns input."""
    logger.debug("echo: input_length=%d", len(text))
    logger.debug("echo returned %d characters", len(text))
    return text


@mcp_tools.tool()
def search_stops(query: str, limit: int = 10) -> list[dict[str, str]]:
    """
    Search transit stops by name.

    Examples:
    - Poznań Główny
    - Rondo Kaponiera
    - os. Sobieskiego
    """
    logger.debug("search_stops: query=%r limit=%d", query, limit)
    schedule: ZTMStaticSchedule = ZTMStaticSchedule.instance()
    matched_stops = [{**asdict(match.stop), "score": match.score} for match in schedule.fuzzy_search_stops(query, limit=limit)]
    logger.debug("search_stops returned %d stops", len(matched_stops))
    return matched_stops


@mcp_tools.tool()
def search_routes(query: str, limit: int = 10) -> list[dict[str, str]]:
    """
    Search routes by number or name.

    Examples:
    - 904
    - 221
    - SYPNIEWO - GARBARY PKM
    """
    schedule: ZTMStaticSchedule = ZTMStaticSchedule.instance()
    logger.debug("search_routes: query=%r limit=%d", query, limit)
    routes = schedule.search_routes(query, limit)
    logger.debug("search_routes returned %d routes", len(routes))
    return routes


@mcp_tools.tool()
def get_routes_for_stop(stop_id: str) -> list[dict[str, Any]]:
    """List static mock routes serving a stop. Resolve stop_id with search_stops first.

    This lists routes in the dataset, regardless of their operating date.
    """
    logger.debug("get_routes_for_stop: stop_id=%r", stop_id)
    schedule = ZTMStaticSchedule.instance()
    if schedule.get_stop(stop_id) is None:
        raise ToolError("Unknown stop_id. Use search_stops to find a valid stop.")
    routes = schedule.get_routes_for_stop(stop_id)
    logger.debug("get_routes_for_stop returned %d routes", len(routes))
    return [asdict(route) for route in sorted(routes, key=lambda route: route.route_id)]


@mcp_tools.tool()
def get_departures(
    stop_id: str,
    route_id: str,
    date: str,
    time: str,
    direction_id: int | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """Return scheduled departures for a line and stop from static mock GTFS.

    Resolve IDs using search_stops and search_routes. Date is the GTFS service
    day (YYYY-MM-DD), and time is HH:MM (00:00 through 23:59), inclusive.
    Overnight departures may exceed 24:00 and belong to that service day;
    departure_datetime shows their actual calendar date. This does not search
    other service days or provide live delays. Direction is optional (0 or 1).
    An empty departures list means no matching departures in that service day.
    """
    logger.debug(
        "get_departures: stop_id=%r route_id=%r date=%r time=%r direction_id=%r limit=%d",
        stop_id, route_id, date, time, direction_id, limit,
    )
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ToolError("Invalid date. Use YYYY-MM-DD.")
    try:
        day = calendar_date.fromisoformat(date)
    except ValueError as exc:
        raise ToolError("Invalid date. Provide a valid calendar date as YYYY-MM-DD.") from exc
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time):
        raise ToolError("Invalid time. Use HH:MM from 00:00 through 23:59.")
    if direction_id is not None and direction_id not in (0, 1):
        raise ToolError("Invalid direction_id. Use 0 or 1, or omit it.")
    if not 1 <= limit <= 100:
        raise ToolError("Invalid limit. Use a number from 1 to 100.")

    schedule = ZTMStaticSchedule.instance()
    stop = schedule.get_stop(stop_id)
    route = schedule.get_route(route_id)
    if stop is None:
        raise ToolError("Unknown stop_id. Use search_stops to find a valid stop.")
    if route is None:
        raise ToolError("Unknown route_id. Use search_routes to find a valid line.")
    hour, minute = map(int, time.split(":"))
    departures = schedule.get_next_departures(
        stop_id, hour * 3600 + minute * 60, day,
        limit=limit, route_id=route_id, direction_id=direction_id,
    )
    logger.debug("get_departures returned %d departures", len(departures))
    return {
        "stop_id": stop.stop_id,
        "stop_name": stop.stop_name,
        "route_id": route.route_id,
        "route_short_name": route.route_short_name,
        "date": date,
        "time": time,
        "direction_id": direction_id,
        "data_source": "static_mock",
        "realtime": False,
        "departures": departures,
    }
