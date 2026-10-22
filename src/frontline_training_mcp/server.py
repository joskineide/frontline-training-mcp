"""Frontline Training MCP server.

Phase 0 skeleton: one stub tool over hard-coded fake data, so the wire
protocol can be inspected before the real domain (Postgres, auth, tenants)
lands in later phases.
"""

import os
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

mcp = MCPServer(
    "frontline-training",
    instructions="Look up training modules for frontline workers.",
)


class Module(BaseModel):
    """One training module, as returned to the model."""

    module_id: str
    title: str
    summary: str
    duration_minutes: int


# Fake data for Phase 0 only. Replaced by tenant-scoped Postgres in Phase 1.
_MODULES: dict[str, Module] = {
    "forklift-safety": Module(
        module_id="forklift-safety",
        title="Forklift Safety",
        summary="Pre-shift inspection, load limits, and pedestrian zones.",
        duration_minutes=25,
    ),
    "food-handling-basics": Module(
        module_id="food-handling-basics",
        title="Food Handling Basics",
        summary="Temperature control, cross-contamination, and handwashing.",
        duration_minutes=15,
    ),
}


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
def get_module(
    module_id: Annotated[str, Field(description="The module's ID, e.g. 'forklift-safety'.")],
) -> Module:
    """Get one training module's title, summary and duration by its ID."""
    module = _MODULES.get(module_id)
    if module is None:
        # ToolError becomes an isError result the model can read and recover from,
        # unlike a JSON-RPC protocol error, which the model never sees.
        raise ToolError(f"No training module with ID {module_id!r}.")
    return module


def main() -> None:
    # Cloud Run injects PORT (default 8080); the same contract works locally.
    mcp.run(transport="streamable-http", port=int(os.environ.get("PORT", "8080")))
