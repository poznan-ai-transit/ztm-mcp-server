# tools.py
from __future__ import annotations

from fastmcp import FastMCP

from services.ztm_static_schedule import ZTMStaticSchedule

mcp_tools: FastMCP = FastMCP("ztm-poznan-tools")


@mcp_tools.tool()
def echo(text: str) -> str:
    """Test tool — returns input."""
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
    schedule: ZTMStaticSchedule = ZTMStaticSchedule.instance()
    return schedule.search_stops(query, limit)


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
    return schedule.search_routes(query, limit)
