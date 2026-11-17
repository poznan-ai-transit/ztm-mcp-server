# resources.py
from __future__ import annotations

from dataclasses import asdict

from fastmcp import Context, FastMCP

from services.ztm_schedule_repository import ScheduleRepository, warsaw_today

mcp_resources: FastMCP = FastMCP("ztm-poznan-resources")


@mcp_resources.resource("resource://ztm_static_storage/list_routes_and_stops")
def list_routes_and_stops(ctx: Context) -> dict[str, list[dict[str, str]]]:
    """List all routes and stops from the feed selected for today in Europe/Warsaw."""
    request_context = ctx.request_context
    if request_context is None:
        raise RuntimeError("Resource requires an active MCP request context")

    repository: ScheduleRepository = request_context.lifespan_context["ztm_static_storage"]
    storage = repository.for_date(warsaw_today())
    routes: list[dict[str, str]] = [asdict(route) for route in storage.get_all_routes()]
    stops: list[dict[str, str]] = [asdict(stop) for stop in storage.get_all_stops()]

    return {"routes": routes, "stops": stops}
