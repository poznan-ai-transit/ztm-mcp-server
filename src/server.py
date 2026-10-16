from __future__ import annotations

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from ini import ini
from logging_config import logger
from mcp_server.lifespans import ztm_service_lifespan
from mcp_server.resources import mcp_resources
from mcp_server.tools import mcp_tools


def create_server() -> FastMCP:
    """Create a configured server without starting its lifespan or transport."""

    logger.info("Creating MCP server")

    mcp = FastMCP(
        "ztm-poznan",
        instructions=(
            "Before calling a tool, check its required information against the user's "
            "message and conversation history. If necessary information is missing, "
            "ask one concise clarification question covering the missing details and "
            "wait for the answer. Reuse details already provided. Never invent a stop, "
            "starting location, line number, ID, date, or time. Ask only for details "
            "needed by the requested operation; optional parameters may be omitted. "
            "For departures, obtain the stop, line, service date, and time. Resolve "
            "stop names and line numbers using search_stops and search_routes; reuse "
            "previously resolved IDs when applicable. If search results leave the "
            "intended stop or route ambiguous, ask the user before selecting an ID. "
            "Resolve relative dates or times only when reliable current date, time, "
            "and Europe/Warsaw timezone context is available; otherwise ask. "
            "These tools provide static schedule lookups, not journey planning."
        ),
        strict_input_validation=True,
        mask_error_details=True,
        lifespan=ztm_service_lifespan,
    )

    logger.debug("Mounting MCP tools")
    mcp.mount(mcp_tools)

    logger.debug("Mounting MCP resources")
    mcp.mount(mcp_resources)

    logger.info("MCP server created")
    return mcp


def run_server(mcp: FastMCP) -> None:
    """Run a configured server using the application's HTTP settings."""
    logger.info(
        "Starting MCP HTTP server on %s:%s (stateless HTTP)", ini.server_host, ini.server_port
    )
    mcp.run(
        transport="http",
        host=ini.server_host,
        port=ini.server_port,
        stateless_http=True,
        middleware=[
            Middleware(
                CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
            )
        ],
    )
    logger.info("MCP HTTP server stopped")
