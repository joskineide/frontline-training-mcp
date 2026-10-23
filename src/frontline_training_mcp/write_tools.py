"""The write tools (docs/design.md §5): the MCP adapter for what a manager can change.

Small, reversible, verifiable writes only (design principle 8); the rules live in
service.py. Publishing an announcement is deliberately impossible here: there is no tool
and no MCP scope for it (D12), and the service refuses non-platform callers anyway.
"""

from collections.abc import Callable
from datetime import date
from typing import Annotated

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from frontline_training_mcp import service
from frontline_training_mcp.db import Database
from frontline_training_mcp.identity import Identity
from frontline_training_mcp.schemas import AssignResult, Draft, DueDateChange
from frontline_training_mcp.tools import MODULE_ID, run

# --- Model-facing text: these are prompts, reviewed like code (tests/snapshots/tools.json) ---

ASSIGN_MODULE = (
    "Assign a training module to a worker on your team, with an optional due date. "
    "Safe to retry: if the worker already has this module open, the existing assignment is "
    "returned unchanged, with a note saying so. To change its due date, use change_due_date. "
    "Get worker IDs from team_progress and module IDs from search_training."
)
CHANGE_DUE_DATE = (
    "Change the due date of a module currently assigned to a worker on your team (not yet "
    "completed). Only use this when the user explicitly asks to change a deadline, for "
    "example after assign_module reports that the module was already assigned."
)
DRAFT_ANNOUNCEMENT = (
    "Save a draft announcement for your team. Drafts are private to you: nobody else sees one "
    "until you publish it yourself in the training platform. After calling, show the user the "
    "full draft so they can review it before publishing."
)
WORKER_ID = "A worker ID from team_progress, e.g. 'ana.souza'."
DUE_DATE = "ISO date (YYYY-MM-DD), today or later."

# Honest hints, per the spec's definitions (D5 reasoning, principle 7).
ASSIGN_HINTS = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
CHANGE_HINTS = ToolAnnotations(  # overwriting a date is a destructive update
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
)
DRAFT_HINTS = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)


def register_write_tools(
    mcp: MCPServer, db: Database, current_identity: Callable[[], Identity]
) -> None:
    @mcp.tool(description=ASSIGN_MODULE, annotations=ASSIGN_HINTS)
    def assign_module(
        module_id: Annotated[str, Field(min_length=1, max_length=100, description=MODULE_ID)],
        worker_id: Annotated[str, Field(min_length=1, max_length=100, description=WORKER_ID)],
        due_date: Annotated[date | None, Field(description=f"Optional. {DUE_DATE}")] = None,
    ) -> AssignResult:
        return run(service.assign_module, db, current_identity(), module_id, worker_id, due_date)

    @mcp.tool(description=CHANGE_DUE_DATE, annotations=CHANGE_HINTS)
    def change_due_date(
        module_id: Annotated[str, Field(min_length=1, max_length=100, description=MODULE_ID)],
        worker_id: Annotated[str, Field(min_length=1, max_length=100, description=WORKER_ID)],
        due_date: Annotated[date, Field(description=DUE_DATE)],
    ) -> DueDateChange:
        return run(service.change_due_date, db, current_identity(), module_id, worker_id, due_date)

    @mcp.tool(description=DRAFT_ANNOUNCEMENT, annotations=DRAFT_HINTS)
    def draft_announcement(
        title: Annotated[str, Field(min_length=1, max_length=120, description="A short title.")],
        body: Annotated[
            str, Field(min_length=1, max_length=2000, description="The announcement text.")
        ],
    ) -> Draft:
        return run(service.draft_announcement, db, current_identity(), title, body)
