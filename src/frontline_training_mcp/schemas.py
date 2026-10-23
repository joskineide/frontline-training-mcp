"""Result shapes shared by every surface (the MCP tools and the platform stand-in).

Only what a reader needs, and never a tenant ID. For MCP tools these classes also become
the published output schemas, so renaming one is a contract change (tests/snapshots).
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

# --- Reads ---


class ModuleSummary(BaseModel):
    module_id: str
    title: str
    summary: str
    duration_minutes: int


class SearchResults(BaseModel):
    results: list[ModuleSummary]


class ModuleDetail(ModuleSummary):
    content: str
    truncated: bool


class AssignmentItem(BaseModel):
    module_id: str
    title: str
    due_date: date | None
    status: Literal["open", "completed"]
    overdue: bool


class AssignmentPage(BaseModel):
    assignments: list[AssignmentItem]
    next_cursor: str | None


class AnnouncementItem(BaseModel):
    announcement_id: int
    title: str
    body: str
    author_name: str
    published_at: datetime


class AnnouncementPage(BaseModel):
    announcements: list[AnnouncementItem]
    next_cursor: str | None


ModuleStatus = Literal["not_assigned", "open", "overdue", "completed"]


class WorkerProgress(BaseModel):
    worker_id: str
    name: str
    open: int
    completed: int
    overdue: int
    module_status: ModuleStatus | None = None


class TeamProgress(BaseModel):
    workers: list[WorkerProgress]
    next_cursor: str | None


# --- Writes ---


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


# --- Platform stand-in only (never exposed over MCP) ---


class Profile(BaseModel):
    user_id: str
    name: str
    role: Literal["worker", "manager"]
    tenant_name: str


class DraftItem(BaseModel):
    draft_id: int
    title: str
    body: str
    created_at: datetime


class DraftList(BaseModel):
    drafts: list[DraftItem]
    team_size: int


class Published(BaseModel):
    announcement_id: int
    title: str
    published_at: datetime
    recipients: int
