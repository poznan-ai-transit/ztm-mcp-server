from __future__ import annotations

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from logging_config import logger
from mcp_server.lifespans import ztm_service_lifespan
from mcp_server.resources import mcp_resources
from mcp_server.tools import mcp_tools


def create_server() -> FastMCP:
    """Create a configured server without starting its lifespan or transport."""

    logger.info("Creating MCP server")

    mcp = FastMCP(
        "ztm-poznan",
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
    logger.info("Starting MCP HTTP server on 0.0.0.0:8000 (stateless HTTP)")
    mcp.run(
        transport="http",
        host="0.0.0.0",
        port=8000,
        stateless_http=True,
        middleware=[
            Middleware(
                CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
            )
        ],
    )
    logger.info("MCP HTTP server stopped")
