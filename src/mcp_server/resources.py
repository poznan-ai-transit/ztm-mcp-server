# resources.py
from __future__ import annotations

from dataclasses import asdict
from datetime import date as calendar_date

from fastmcp import Context, FastMCP

from services.ztm_schedule_repository import ScheduleRepository

mcp_resources: FastMCP = FastMCP("ztm-poznan-resources")


@mcp_resources.resource(
    "resource://ztm_static_storage/list_routes_and_stops/{date}", mime_type="application/json"
)
def list_routes_and_stops(date: str, ctx: Context) -> dict[str, list[dict[str, str]]]:
    """List all routes and stops from the feed covering date (YYYY-MM-DD).

    Returns the entire selected feed, including routes and stops without service
    on that day. The date must be covered by a loaded schedule.
    """
    try:
        day = calendar_date.fromisoformat(date)
    except ValueError as exc:
        raise ValueError("Invalid date. Use a valid calendar date in YYYY-MM-DD format.") from exc
    if day.isoformat() != date:
        raise ValueError("Invalid date. Use YYYY-MM-DD.")
    request_context = ctx.request_context
    if request_context is None:
        raise RuntimeError("Resource requires an active MCP request context")

    repository: ScheduleRepository = request_context.lifespan_context["ztm_static_storage"]
    storage = repository.for_date(day)
    routes: list[dict[str, str]] = [asdict(route) for route in storage.get_all_routes()]
    stops: list[dict[str, str]] = [asdict(stop) for stop in storage.get_all_stops()]

    return {"routes": routes, "stops": stops}
