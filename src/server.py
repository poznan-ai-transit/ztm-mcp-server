from __future__ import annotations

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from services.static_storage import StaticStorage
from services.ztm_service import ZTMService

mcp = FastMCP("ztm-poznan")
ztm_service = ZTMService()
static_storage = StaticStorage()


@mcp.tool()
def echo(tekst: str) -> str:
    """Test tool — returns input."""
    return tekst


@mcp.tool()
def list_routes_and_stops() -> dict[str, list[dict[str, str]]]:
    """List all routes and stops from static GTFS data."""
    return {
        "routes": static_storage.get_routes(),
        "stops": static_storage.get_stops(),
    }


if __name__ == "__main__":
    ztm_service.start_daily_refresh(static_storage)
    mcp.run(
        transport="sse",
        host="0.0.0.0",
        port=8000,
        middleware=[Middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])],
    )

# HOW TO TEST:
# run npx @modelcontextprotocol/inspector (node.js has to be installed)
# in url set http://localhost:8000/sse and set transport type: SSE
# then connect