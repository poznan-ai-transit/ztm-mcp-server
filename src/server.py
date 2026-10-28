from __future__ import annotations

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from ini import ini
from logging_config import logger
from mcp_server.lifespans import ztm_service_lifespan
from mcp_server.resources import mcp_resources
from mcp_server.tools import mcp_tools

SERVER_INSTRUCTIONS = """
This MCP server provides information about public transport in Poznań,
Poland, operated by ZTM Poznań.

Tool usage:
- Use search_stops to verify stop names and find matching stops.
- Use search_routes to verify route numbers and find matching routes.
- Use the appropriate tool when answering factual questions about stops
  or routes. Do not rely on model memory for this information.
- Use the tool arguments to search for the stop name or route number
  provided by the user.
- Treat tool results as the source of truth. Do not invent results or
  claim that a stop or route exists when the tools do not confirm it.
- When there are multiple stops with the same name, always consider
  routes from all of them.

Limitations:
- This server does not provide live departure information.
- Do not infer departure times from stop or route search results.
- The server's domain is Poznań public transport; it is not a general
  public transport service for other cities.
"""


def create_server() -> FastMCP:
    """Create a configured server without starting its lifespan or transport."""

    logger.info("Creating MCP server")

    mcp = FastMCP(
        "ztm-poznan",
        instructions=SERVER_INSTRUCTIONS,
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
