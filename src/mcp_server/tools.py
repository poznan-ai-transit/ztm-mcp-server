# tools.py
from __future__ import annotations

from fastmcp import FastMCP

mcp_tools: FastMCP = FastMCP("ztm-poznan-tools")


@mcp_tools.tool()
def echo(text: str) -> str:
    """Test tool — returns input."""
    return text