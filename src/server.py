# server.py
from __future__ import annotations

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

from mcp_server.lifespans import ztm_service_lifespan
from mcp_server.resources import mcp_resources
from mcp_server.tools import mcp_tools

mcp: FastMCP = FastMCP(
    "ztm-poznan",
    strict_input_validation=True,
    mask_error_details=True,
    lifespan=ztm_service_lifespan,
)

mcp.mount(mcp_tools)
mcp.mount(mcp_resources)


if __name__ == "__main__":
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

# HOW TO TEST:
# run npx @modelcontextprotocol/inspector
# In MCP Inspector set:
# Transport Type: Streamable HTTP
# URL: http://localhost:8000/mcp
# Stateless HTTP is enabled to avoid missing session ID errors.
