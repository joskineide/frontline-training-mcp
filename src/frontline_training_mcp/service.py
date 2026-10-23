"""Business logic shared by every surface: authorization, tenant-scoped queries and writes.

The MCP tools and the platform stand-in are thin adapters over these functions, so both
enforce exactly the same rules. Every function takes the caller's Identity, checks its
scope where one applies (profile needs none: it reads only the caller's own row), and works
inside one transaction scoped to the caller's tenant; every query states its tenant filter
(design principle 9). Expected failures raise a ServiceError whose message
is written for the caller; each adapter maps it to its own error shape.
"""

import base64
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import Row, and_, case, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from frontline_training_mcp.db import Database
from frontline_training_mcp.identity import Identity
from frontline_training_mcp.models import (
    Announcement,
    AppUser,
    Assignment,
    AuditLog,
    Module,
    Team,
    Tenant,
)
from frontline_training_mcp.schemas import (
    AnnouncementItem,
    AnnouncementPage,
    AssignmentInfo,
    AssignmentItem,
    AssignmentPage,
    AssignResult,
    Draft,
    DraftItem,
    DraftList,
    DueDateChange,
    ModuleDetail,
    ModuleStatus,
    ModuleSummary,
    Profile,
    Published,
    SearchResults,
    TeamProgress,
    WorkerProgress,
)

CONTENT_LIMIT = 4_000  # characters, about 1,000 tokens: a starting budget, to be measured
DRAFTS_LIMIT = 50  # the newest drafts; the stand-in shows no more than that
BIGINT_MAX = 2**63 - 1  # announcement IDs are bigint
NO_SUCH_DRAFT = "No such draft."  # one answer for every ID the caller can't publish


class ServiceError(Exception):
    """An expected failure, with a message written for the caller (a model or a person)."""


class Forbidden(ServiceError):
    pass


class NotFound(ServiceError):
    pass


class InvalidRequest(ServiceError):
    pass


class Conflict(ServiceError):
    pass


# --- Helpers ---


def require_scope(me: Identity, scope: str) -> None:
    if scope not in me.scopes:
        raise Forbidden("You don't have permission to do this.")


def _cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"offset:{offset}".encode()).decode()


def _offset(cursor: str | None) -> int:
    """Cursors are opaque to the caller. A tampered one can only move within the caller's
    own, tenant-filtered rows, never widen them."""
    if not cursor:
        return 0
    try:
        kind, _, value = base64.urlsafe_b64decode(cursor).decode().partition(":")
        offset = int(value)
    except ValueError:  # bad base64, bad text or bad number
        kind, offset = "", -1
    if kind != "offset" or offset < 0:
        raise InvalidRequest("Invalid cursor: pass next_cursor from the previous call unchanged.")
    return offset


def _page(rows: Sequence[Row], offset: int, limit: int) -> tuple[Sequence[Row], str | None]:
    """Rows are fetched with limit + 1: the extra row only signals that another page exists."""
    next_cursor = _cursor(offset + limit) if len(rows) > limit else None
    return rows[:limit], next_cursor


def _led_team(session: Session, me: Identity) -> str | None:
    return session.scalar(
        select(Team.team_id).where(Team.tenant_id == me.tenant_id, Team.manager_id == me.user_id)
    )


def _own_team(session: Session, me: Identity) -> str | None:
    """The team a worker belongs to, or the team a manager leads."""
    member_of = session.scalar(
        select(AppUser.team_id).where(
            AppUser.tenant_id == me.tenant_id, AppUser.user_id == me.user_id
        )
    )
    return member_of or _led_team(session, me)


def _team_size(session: Session, me: Identity, team_id: str) -> int:
    return session.scalar(
        select(func.count())
        .select_from(AppUser)
        .where(
            AppUser.tenant_id == me.tenant_id, AppUser.team_id == team_id, AppUser.role == "worker"
        )
    )


def _overdue():
    return and_(Assignment.completed_at.is_(None), Assignment.due_date < func.current_date())


def _module_statuses(
    session: Session, me: Identity, module_id: str, worker_ids: list[str]
) -> dict[str, ModuleStatus]:
    rows = session.execute(
        select(Assignment.worker_id, Assignment.completed_at, _overdue().label("overdue")).where(
            Assignment.tenant_id == me.tenant_id,
            Assignment.module_id == module_id,
            Assignment.worker_id.in_(worker_ids),
        )
    ).all()
    statuses: dict[str, ModuleStatus] = {}
    for row in rows:
        if row.completed_at is None:  # the one open assignment wins over past completions
            statuses[row.worker_id] = "overdue" if row.overdue else "open"
        else:
            statuses.setdefault(row.worker_id, "completed")
    return statuses


def _audit(session: Session, me: Identity, action: str, target: dict[str, Any], outcome: str):
    """Record a write in the same transaction as the write itself. IDs only, never free text."""
    session.execute(
        insert(AuditLog).values(
            tenant_id=me.tenant_id, actor_id=me.user_id, tool=action, target=target, outcome=outcome
        )
    )


def _manager_context(session: Session, me: Identity, worker_id: str | None = None) -> str:
    """The team the caller leads; if a worker is named, they must be on it."""
    team_id = _led_team(session, me)
    if team_id is None:
        raise Forbidden("You don't lead a team.")
    if worker_id is not None and not session.scalar(
        select(AppUser.user_id).where(
            AppUser.tenant_id == me.tenant_id,
            AppUser.user_id == worker_id,
            AppUser.team_id == team_id,
            AppUser.role == "worker",
        )
    ):
        # The same answer for unknown workers, other teams and other tenants.
        raise NotFound(f"No worker with ID {worker_id!r} on your team.")
    return team_id


def _check_module(session: Session, me: Identity, module_id: str) -> None:
    if not session.scalar(
        select(Module.module_id).where(
            Module.tenant_id == me.tenant_id, Module.module_id == module_id
        )
    ):
        # Identical whether the ID doesn't exist or belongs to another tenant (D1).
        raise NotFound(f"No training module with ID {module_id!r}.")


def _check_due_date(session: Session, due_date: date | None) -> None:
    # "Today" comes from the database, the same clock that decides what is overdue.
    if due_date is not None and due_date < session.scalar(select(func.current_date())):
        raise InvalidRequest("The due date is in the past.")


def _open_assignment(session: Session, me: Identity, worker_id: str, module_id: str, lock=False):
    query = select(Assignment.assignment_id, Assignment.due_date).where(
        Assignment.tenant_id == me.tenant_id,
        Assignment.worker_id == worker_id,
        Assignment.module_id == module_id,
        Assignment.completed_at.is_(None),
    )
    return session.execute(query.with_for_update() if lock else query).one_or_none()


# --- Reads ---


def search_training(db: Database, me: Identity, query: str, limit: int) -> SearchResults:
    require_scope(me, "training:read")
    if not query.strip():
        raise InvalidRequest("The search query is empty.")
    # A bound parameter, parsed by websearch_to_tsquery: any text is safe and valid (T10).
    terms = func.websearch_to_tsquery("english", query)
    with db.tenant_session(me.tenant_id, read_only=True) as session:
        rows = session.execute(
            select(Module.module_id, Module.title, Module.summary, Module.duration_minutes)
            .where(Module.tenant_id == me.tenant_id, Module.search_vector.op("@@")(terms))
            .order_by(func.ts_rank(Module.search_vector, terms).desc(), Module.module_id)
            .limit(limit)
        ).all()
    return SearchResults(results=[ModuleSummary(**row._mapping) for row in rows])


def get_module(db: Database, me: Identity, module_id: str) -> ModuleDetail:
    require_scope(me, "training:read")
    with db.tenant_session(me.tenant_id, read_only=True) as session:
        row = session.execute(
            select(
                Module.module_id,
                Module.title,
                Module.summary,
                Module.duration_minutes,
                Module.content,
            ).where(Module.tenant_id == me.tenant_id, Module.module_id == module_id)
        ).one_or_none()
    if row is None:
        # Identical whether the ID doesn't exist or belongs to another tenant (D1).
        raise NotFound(f"No training module with ID {module_id!r}.")
    return ModuleDetail(
        module_id=row.module_id,
        title=row.title,
        summary=row.summary,
        duration_minutes=row.duration_minutes,
        content=row.content[:CONTENT_LIMIT],
        truncated=len(row.content) > CONTENT_LIMIT,
    )


def my_assignments(
    db: Database, me: Identity, status: str, limit: int, cursor: str | None
) -> AssignmentPage:
    require_scope(me, "training:read")
    offset = _offset(cursor)
    query = (
        select(
            Assignment.module_id,
            Module.title,
            Assignment.due_date,
            Assignment.completed_at,
            case((_overdue(), True), else_=False).label("overdue"),
        )
        .join(
            Module,
            and_(
                Module.tenant_id == Assignment.tenant_id,
                Module.module_id == Assignment.module_id,
            ),
        )
        .where(Assignment.tenant_id == me.tenant_id, Assignment.worker_id == me.user_id)
    )
    if status == "open":
        query = query.where(Assignment.completed_at.is_(None))
    elif status == "completed":
        query = query.where(Assignment.completed_at.is_not(None))
    query = query.order_by(
        Assignment.completed_at.desc().nulls_first(),  # open first, then latest completions
        Assignment.due_date.asc().nulls_last(),
        Assignment.module_id,
    )
    with db.tenant_session(me.tenant_id, read_only=True) as session:
        rows = session.execute(query.offset(offset).limit(limit + 1)).all()
    rows, next_cursor = _page(rows, offset, limit)
    return AssignmentPage(
        assignments=[
            AssignmentItem(
                module_id=row.module_id,
                title=row.title,
                due_date=row.due_date,
                status="completed" if row.completed_at else "open",
                overdue=row.overdue,
            )
            for row in rows
        ],
        next_cursor=next_cursor,
    )


def list_announcements(
    db: Database, me: Identity, limit: int, cursor: str | None
) -> AnnouncementPage:
    require_scope(me, "training:read")
    offset = _offset(cursor)
    with db.tenant_session(me.tenant_id, read_only=True) as session:
        team_id = _own_team(session, me)
        if team_id is None:
            return AnnouncementPage(announcements=[], next_cursor=None)
        rows = session.execute(
            select(
                Announcement.announcement_id,
                Announcement.title,
                Announcement.body,
                AppUser.display_name.label("author_name"),
                Announcement.published_at,
            )
            .join(
                AppUser,
                and_(
                    AppUser.tenant_id == Announcement.tenant_id,
                    AppUser.user_id == Announcement.author_id,
                ),
            )
            .where(
                Announcement.tenant_id == me.tenant_id,
                Announcement.team_id == team_id,
                Announcement.status == "published",  # drafts are private to their author
            )
            .order_by(Announcement.published_at.desc(), Announcement.announcement_id.desc())
            .offset(offset)
            .limit(limit + 1)
        ).all()
    rows, next_cursor = _page(rows, offset, limit)
    return AnnouncementPage(
        announcements=[AnnouncementItem(**row._mapping) for row in rows],
        next_cursor=next_cursor,
    )


def team_progress(
    db: Database, me: Identity, module_id: str | None, limit: int, cursor: str | None
) -> TeamProgress:
    require_scope(me, "team:read")
    offset = _offset(cursor)
    counted = Assignment.assignment_id
    with db.tenant_session(me.tenant_id, read_only=True) as session:
        team_id = _led_team(session, me)
        if team_id is None:
            raise Forbidden("You don't lead a team.")
        if module_id is not None:
            _check_module(session, me, module_id)
        rows = session.execute(
            select(
                AppUser.user_id,
                AppUser.display_name,
                func.count(counted).filter(Assignment.completed_at.is_(None)).label("open"),
                func.count(counted).filter(Assignment.completed_at.is_not(None)).label("completed"),
                func.count(counted).filter(_overdue()).label("overdue"),
            )
            .outerjoin(
                Assignment,
                and_(
                    Assignment.tenant_id == AppUser.tenant_id,
                    Assignment.worker_id == AppUser.user_id,
                ),
            )
            .where(
                AppUser.tenant_id == me.tenant_id,
                AppUser.team_id == team_id,
                AppUser.role == "worker",
            )
            .group_by(AppUser.user_id, AppUser.display_name)
            .order_by(AppUser.display_name, AppUser.user_id)
            .offset(offset)
            .limit(limit + 1)
        ).all()
        rows, next_cursor = _page(rows, offset, limit)
        statuses = (
            _module_statuses(session, me, module_id, [row.user_id for row in rows])
            if module_id
            else {}
        )
    return TeamProgress(
        workers=[
            WorkerProgress(
                worker_id=row.user_id,
                name=row.display_name,
                open=row.open,
                completed=row.completed,
                overdue=row.overdue,
                module_status=statuses.get(row.user_id, "not_assigned") if module_id else None,
            )
            for row in rows
        ],
        next_cursor=next_cursor,
    )


# --- Writes (small, reversible, verifiable: design principle 8) ---


def assign_module(
    db: Database, me: Identity, module_id: str, worker_id: str, due_date: date | None
) -> AssignResult:
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


def change_due_date(
    db: Database, me: Identity, module_id: str, worker_id: str, due_date: date
) -> DueDateChange:
    require_scope(me, "assignments:write")
    with db.tenant_session(me.tenant_id, read_only=False) as session:
        _manager_context(session, me, worker_id)
        _check_due_date(session, due_date)
        # Lock the row, so a concurrent change can't slip between reading and writing it.
        current = _open_assignment(session, me, worker_id, module_id, lock=True)
        if current is None:
            raise NotFound(f"No open assignment of {module_id!r} for {worker_id!r} on your team.")
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


def draft_announcement(db: Database, me: Identity, title: str, body: str) -> Draft:
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
                status="draft",  # a draft is all the AI can ever create (D12)
            )
            .returning(Announcement.announcement_id, Announcement.created_at)
        ).one()
        team_size = _team_size(session, me, team_id)
        _audit(session, me, "draft_announcement", {"draft_id": row.announcement_id}, "drafted")
    return Draft(
        draft_id=row.announcement_id,
        title=title,
        body=body,
        team_size=team_size,
        created_at=row.created_at,
    )


# --- Platform stand-in only: never reachable over MCP (D12, D14) ---


def profile(db: Database, me: Identity) -> Profile:
    with db.tenant_session(me.tenant_id, read_only=True) as session:
        row = session.execute(
            select(AppUser.user_id, AppUser.display_name, AppUser.role, Tenant.name)
            .join(Tenant, Tenant.tenant_id == AppUser.tenant_id)
            .where(AppUser.tenant_id == me.tenant_id, AppUser.user_id == me.user_id)
        ).one()
    return Profile(
        user_id=row.user_id,
        name=row.display_name,
        role=row.role,
        tenant_name=row.name,
    )


def my_drafts(db: Database, me: Identity) -> DraftList:
    require_scope(me, "announcements:draft")
    with db.tenant_session(me.tenant_id, read_only=True) as session:
        team_id = _manager_context(session, me)
        rows = session.execute(
            select(
                Announcement.announcement_id.label("draft_id"),
                Announcement.title,
                Announcement.body,
                Announcement.created_at,
            )
            .where(
                Announcement.tenant_id == me.tenant_id,
                Announcement.author_id == me.user_id,
                # The same rule publish_draft applies: list exactly what can be published,
                # so the recipient count shown is that draft's real audience.
                Announcement.team_id == team_id,
                Announcement.status == "draft",
            )
            .order_by(Announcement.created_at.desc(), Announcement.announcement_id.desc())
            .limit(DRAFTS_LIMIT + 1)  # the extra row only signals that older drafts exist
        ).all()
        team_size = _team_size(session, me, team_id)
    return DraftList(
        drafts=[DraftItem(**row._mapping) for row in rows[:DRAFTS_LIMIT]],
        team_size=team_size,
        more=len(rows) > DRAFTS_LIMIT,
    )


def publish_draft(db: Database, me: Identity, draft_id: int | None) -> Published:
    """The human step that commits a draft (principle 8). Two independent locks keep it away
    from MCP: only platform identities can call it, and only they can hold the scope.

    `draft_id` is None when the caller sent something that isn't an ID at all; it gets the
    same answer as an unknown ID, after the same checks."""
    if me.audience != "platform":
        raise Forbidden("Announcements can only be published in the training platform.")
    require_scope(me, "announcements:publish")
    with db.tenant_session(me.tenant_id, read_only=False) as session:
        team_id = _manager_context(session, me)
        if draft_id is None or not 0 < draft_id <= BIGINT_MAX:
            raise NotFound(NO_SUCH_DRAFT)
        # One conditional UPDATE is the referee: of two simultaneous publishes, exactly one
        # finds the row still a draft.
        row = session.execute(
            update(Announcement)
            .where(
                Announcement.tenant_id == me.tenant_id,
                Announcement.announcement_id == draft_id,
                Announcement.author_id == me.user_id,
                Announcement.team_id == team_id,
                Announcement.status == "draft",
            )
            .values(status="published", published_at=func.now(), published_by=me.user_id)
            .returning(Announcement.announcement_id, Announcement.title, Announcement.published_at)
        ).one_or_none()
        if row is None:
            already = session.scalar(
                select(Announcement.announcement_id).where(
                    Announcement.tenant_id == me.tenant_id,
                    Announcement.announcement_id == draft_id,
                    Announcement.author_id == me.user_id,
                    Announcement.status == "published",
                )
            )
            if already:
                raise Conflict("This announcement is already published.")
            # The same answer for unknown IDs, other authors, other teams and other tenants.
            raise NotFound(NO_SUCH_DRAFT)
        recipients = _team_size(session, me, team_id)
        _audit(session, me, "platform:publish_draft", {"announcement_id": draft_id}, "published")
    return Published(
        announcement_id=row.announcement_id,
        title=row.title,
        published_at=row.published_at,
        recipients=recipients,
    )
