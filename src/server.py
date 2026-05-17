from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

mcp = FastMCP("ztm-poznan")


@mcp.tool()
def echo(tekst: str) -> str:
    """Test tool — returns input."""
    return tekst


if __name__ == "__main__":
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