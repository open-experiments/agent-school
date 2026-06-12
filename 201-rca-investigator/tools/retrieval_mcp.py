"""MCP server exposing the RCA skills (stdio transport)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastmcp import FastMCP  # noqa: E402

from tools import lib  # noqa: E402

mcp = FastMCP("rca-retrieval")


@mcp.tool()
def get_incident(component: str = "all") -> str:
    """Fetch the structured alert feed for a component (AMF|SMF|UPF|all)."""
    return lib.get_incident(component=component)


@mcp.tool()
def retrieve_evidence(query: str, nf: str = "all", k: int = 5) -> str:
    """Retrieve telemetry and alert records from the RAG backend."""
    return lib.retrieve_evidence(query=query, nf=nf, k=k)


if __name__ == "__main__":
    mcp.run()
