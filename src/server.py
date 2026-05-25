from __future__ import annotations

from collections.abc import Mapping
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from services.static_storage import ReadOnlyStaticStorage, StaticStorage
from services.ztm_service import ZTMService

mcp = FastMCP("ztm-poznan")
ztm_service = ZTMService.instance()
static_storage: ReadOnlyStaticStorage = StaticStorage.instance()


def _to_plain(value):
    if isinstance(value, Mapping):
        return {key: _to_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(item) for item in value]
    return value


@mcp.tool()
def echo(text: str) -> str:
    """Test tool — returns input."""
    return text


@mcp.tool()
def list_routes_and_stops() -> dict[str, list[dict[str, str]]]:
    """List all routes and stops from static GTFS data."""
    with static_storage.read_lock():
        routes = static_storage.get_routes()
        stops = static_storage.get_stops()
    return {
        "routes": _to_plain(routes),
        "stops": _to_plain(stops),
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