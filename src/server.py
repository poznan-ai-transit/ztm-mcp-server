from __future__ import annotations

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from mcp_server.lifespans import ztm_service_lifespan
from mcp_server.resources import mcp_resources
from mcp_server.tools import mcp_tools


def create_server() -> FastMCP:
    """Create a configured server without starting its lifespan or transport."""
    mcp = FastMCP(
        "ztm-poznan",
        strict_input_validation=True,
        mask_error_details=True,
        lifespan=ztm_service_lifespan,
    )

    mcp.mount(mcp_tools)
    mcp.mount(mcp_resources)
    return mcp


def run_server(mcp: FastMCP) -> None:
    """Run a configured server using the application's HTTP settings."""
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
