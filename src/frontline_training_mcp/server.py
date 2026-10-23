"""Frontline Training MCP server: wiring and the entry point."""

import os
from collections.abc import Callable
from importlib.metadata import version

from mcp.server import MCPServer

from frontline_training_mcp.db import Database
from frontline_training_mcp.identity import Identity, dev_identity
from frontline_training_mcp.tools import register_read_tools

INSTRUCTIONS = (
    "Training for frontline workers and their managers: search and read training modules, "
    "check assignments and team progress, and read team announcements. "
    "Module IDs come from search_training or my_assignments."
)


def create_server(db: Database, current_identity: Callable[[], Identity]) -> MCPServer:
    """Build the server. Its dependencies are passed in, so tests supply their own."""
    mcp = MCPServer(
        "frontline-training", version=version("frontline-training-mcp"), instructions=INSTRUCTIONS
    )
    register_read_tools(mcp, db, current_identity)
    return mcp


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is not set.")
    db = Database(database_url)
    try:
        caller = dev_identity(db)  # there is no login yet: refuse to start outside dev mode
    except (RuntimeError, LookupError) as exc:
        raise SystemExit(str(exc)) from exc
    server = create_server(db, lambda: caller)
    # Cloud Run injects PORT (default 8080); the same contract works locally.
    server.run(transport="streamable-http", port=int(os.environ.get("PORT", "8080")))
