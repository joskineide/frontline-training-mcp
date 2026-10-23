"""The write tools (docs/design.md §5): what a manager can change through the AI.

Small, reversible, verifiable writes only (design principle 8). Each write and its audit row
commit in the same transaction, so there is never a change without its record. Publishing
an announcement is deliberately impossible here: there is no tool and no scope for it (D12).
"""

from collections.abc import Callable
from datetime import date, datetime
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from frontline_training_mcp.db import Database
from frontline_training_mcp.identity import Identity
from frontline_training_mcp.models import Announcement, AppUser, Assignment, AuditLog, Module
from frontline_training_mcp.tools import MODULE_ID, led_team, require_scope

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


class AssignmentInfo(BaseModel):
    module_id: str
    worker_id: str
    due_date: date | None
    status: Literal["open", "completed"]


class AssignResult(BaseModel):
    assignment: AssignmentInfo
    created: bool
    note: str | None = None


class DueDateChange(BaseModel):
    assignment: AssignmentInfo
    previous_due_date: date | None


class Draft(BaseModel):
    draft_id: int
    title: str
    body: str
    team_size: int
    created_at: datetime


# --- Helpers ---


def _audit(session: Session, me: Identity, tool: str, target: dict[str, Any], outcome: str):
    """Record a write in the same transaction as the write itself. IDs only, never free text."""
    session.execute(
        insert(AuditLog).values(
            tenant_id=me.tenant_id, actor_id=me.user_id, tool=tool, target=target, outcome=outcome
        )
    )


def _manager_context(session: Session, me: Identity, worker_id: str | None = None) -> str:
    """The team the caller leads; if a worker is named, they must be on it."""
    team_id = led_team(session, me)
    if team_id is None:
        raise ToolError("You don't lead a team.")
    if worker_id is not None and not session.scalar(
        select(AppUser.user_id).where(
            AppUser.tenant_id == me.tenant_id,
            AppUser.user_id == worker_id,
            AppUser.team_id == team_id,
            AppUser.role == "worker",
        )
    ):
        # The same answer for unknown workers, other teams and other tenants.
        raise ToolError(f"No worker with ID {worker_id!r} on your team.")
    return team_id


def _check_module(session: Session, me: Identity, module_id: str) -> None:
    if not session.scalar(
        select(Module.module_id).where(
            Module.tenant_id == me.tenant_id, Module.module_id == module_id
        )
    ):
        raise ToolError(f"No training module with ID {module_id!r}.")


def _check_due_date(session: Session, due_date: date | None) -> None:
    # "Today" comes from the database, the same clock that decides what is overdue.
    if due_date is not None and due_date < session.scalar(select(func.current_date())):
        raise ToolError("The due date is in the past.")


def _open_assignment(session: Session, me: Identity, worker_id: str, module_id: str, lock=False):
    query = select(Assignment.assignment_id, Assignment.due_date).where(
        Assignment.tenant_id == me.tenant_id,
        Assignment.worker_id == worker_id,
        Assignment.module_id == module_id,
        Assignment.completed_at.is_(None),
    )
    return session.execute(query.with_for_update() if lock else query).one_or_none()


# --- Tools ---


def register_write_tools(
    mcp: MCPServer, db: Database, current_identity: Callable[[], Identity]
) -> None:
    @mcp.tool(description=ASSIGN_MODULE, annotations=ASSIGN_HINTS)
    def assign_module(
        module_id: Annotated[str, Field(min_length=1, max_length=100, description=MODULE_ID)],
        worker_id: Annotated[str, Field(min_length=1, max_length=100, description=WORKER_ID)],
        due_date: Annotated[date | None, Field(description=f"Optional. {DUE_DATE}")] = None,
    ) -> AssignResult:
        me = current_identity()
        require_scope(me, "assignments:write")
        with db.tenant_session(me.tenant_id, read_only=False) as session:
            _manager_context(session, me, worker_id)
            _check_module(session, me, module_id)
            _check_due_date(session, due_date)
            # The partial unique index is the referee: of two simultaneous calls, exactly one
            # inserts, and the other finds the existing row (no check-then-insert race).
            inserted = session.scalar(
                pg_insert(Assignment)
                .values(
                    tenant_id=me.tenant_id,
                    worker_id=worker_id,
                    module_id=module_id,
                    assigned_by=me.user_id,
                    due_date=due_date,
                )
                .on_conflict_do_nothing(
                    index_elements=["tenant_id", "worker_id", "module_id"],
                    index_where=Assignment.completed_at.is_(None),
                )
                .returning(Assignment.assignment_id)
            )
            current = _open_assignment(session, me, worker_id, module_id)
            created = inserted is not None
            _audit(
                session,
                me,
                "assign_module",
                {"module_id": module_id, "worker_id": worker_id},
                "created" if created else "already_assigned",
            )
        note = None
        if not created:
            due = f"due {current.due_date}" if current.due_date else "with no due date"
            note = f"Already assigned, {due}."
            if due_date is not None and due_date != current.due_date:
                note += " The due date was not changed; use change_due_date to change it."
        return AssignResult(
            assignment=AssignmentInfo(
                module_id=module_id, worker_id=worker_id, due_date=current.due_date, status="open"
            ),
            created=created,
            note=note,
        )

    @mcp.tool(description=CHANGE_DUE_DATE, annotations=CHANGE_HINTS)
    def change_due_date(
        module_id: Annotated[str, Field(min_length=1, max_length=100, description=MODULE_ID)],
        worker_id: Annotated[str, Field(min_length=1, max_length=100, description=WORKER_ID)],
        due_date: Annotated[date, Field(description=DUE_DATE)],
    ) -> DueDateChange:
        me = current_identity()
        require_scope(me, "assignments:write")
        with db.tenant_session(me.tenant_id, read_only=False) as session:
            _manager_context(session, me, worker_id)
            _check_due_date(session, due_date)
            # Lock the row, so a concurrent change can't slip between reading and writing it.
            current = _open_assignment(session, me, worker_id, module_id, lock=True)
            if current is None:
                raise ToolError(
                    f"No open assignment of {module_id!r} for {worker_id!r} on your team."
                )
            session.execute(
                update(Assignment)
                .where(
                    Assignment.tenant_id == me.tenant_id,
                    Assignment.assignment_id == current.assignment_id,
                )
                .values(due_date=due_date)
            )
            _audit(
                session,
                me,
                "change_due_date",
                {
                    "module_id": module_id,
                    "worker_id": worker_id,
                    "from": current.due_date.isoformat() if current.due_date else None,
                    "to": due_date.isoformat(),
                },
                "changed",
            )
        return DueDateChange(
            assignment=AssignmentInfo(
                module_id=module_id, worker_id=worker_id, due_date=due_date, status="open"
            ),
            previous_due_date=current.due_date,
        )

    @mcp.tool(description=DRAFT_ANNOUNCEMENT, annotations=DRAFT_HINTS)
    def draft_announcement(
        title: Annotated[str, Field(min_length=1, max_length=120, description="A short title.")],
        body: Annotated[
            str, Field(min_length=1, max_length=2000, description="The announcement text.")
        ],
    ) -> Draft:
        me = current_identity()
        require_scope(me, "announcements:draft")
        with db.tenant_session(me.tenant_id, read_only=False) as session:
            team_id = _manager_context(session, me)
            row = session.execute(
                insert(Announcement)
                .values(
                    tenant_id=me.tenant_id,
                    team_id=team_id,
                    author_id=me.user_id,
                    title=title,
                    body=body,
                    status="draft",  # nothing here can ever set "published" (D12)
                )
                .returning(Announcement.announcement_id, Announcement.created_at)
            ).one()
            team_size = session.scalar(
                select(func.count())
                .select_from(AppUser)
                .where(
                    AppUser.tenant_id == me.tenant_id,
                    AppUser.team_id == team_id,
                    AppUser.role == "worker",
                )
            )
            _audit(session, me, "draft_announcement", {"draft_id": row.announcement_id}, "drafted")
        return Draft(
            draft_id=row.announcement_id,
            title=title,
            body=body,
            team_size=team_size,
            created_at=row.created_at,
        )
