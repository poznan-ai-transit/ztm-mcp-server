from __future__ import annotations
from collections.abc import Mapping, AsyncIterator
from typing import Any, Generator
from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from services.static_storage import ReadOnlyStaticStorage, StaticStorage, WriteOnlyStaticStorage
from services.ztm_service import ZTMService

@lifespan
async def ztm_service_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Initializes lifespan for ztm_service and static_storage"""
    ztm_service: ZTMService = ZTMService.instance()
    static_storage: WriteOnlyStaticStorage = StaticStorage.instance()
    ztm_service.start_daily_refresh(static_storage)

    yield {"static_storage" :static_storage}

    # ztm.service.stop_daily_refresh()

mcp: FastMCP = FastMCP(
    "ztm-poznan",
    strict_input_validation=True,
    mask_error_details=True,
    lifespan=ztm_service_lifespan)

@mcp.tool()
def echo(text: str) -> str:
    """Test tool — returns input."""
    return text


@mcp.tool()
def list_routes_and_stops() -> dict[str, list[dict[str, str]]]:
    """List all routes and stops from static GTFS data."""
    static_storage: ReadOnlyStaticStorage = StaticStorage.instance()
    with static_storage.read_lock():
        routes = static_storage.get_routes()
        stops = static_storage.get_stops()
    return {
        "routes": StaticStorage.to_plain(routes),
        "stops": StaticStorage.to_plain(stops),
    }


if __name__ == "__main__":
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=8000,
        stateless_http=True,
        middleware=[Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])],
    )

# HOW TO TEST:
# run npx @modelcontextprotocol/inspector
# In MCP Inspector set:
# Transport Type: Streamable HTTP
# URL: http://localhost:8000/mcp
# Stateless HTTP is enabled to avoid missing session ID errors.