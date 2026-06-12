"""MCP server exposing runbook search (stdio transport)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastmcp import FastMCP  # noqa: E402

from tools import lib  # noqa: E402

mcp = FastMCP("noc-runbooks")


@mcp.tool()
def search_runbooks(query: str, top_k: int = 2) -> str:
    """Search operational runbooks for remediation guidance."""
    return lib.search_runbooks(query=query, top_k=top_k)


if __name__ == "__main__":
    mcp.run()
