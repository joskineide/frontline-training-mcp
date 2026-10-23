"""The write tools: idempotency, the race, recertification, team scoping and the audit trail."""

import anyio
import pytest
from sqlalchemy import select
from support import call, requires_db, seeded_database

from frontline_training_mcp.models import AuditLog

pytestmark = [requires_db, pytest.mark.anyio]

MARCOS = "acme/marcos.reis"  # manages Store 12: ana.souza, bea.costa, joao.lima
FUTURE = "2099-12-01"


@pytest.fixture
def database():
    """A fresh seeded database per test: these tests write."""
    with seeded_database() as database:
        yield database


def audit_trail(database, tenant="acme"):
    with database.tenant_session(tenant, read_only=True) as session:
        return session.execute(
            select(AuditLog.tool, AuditLog.target, AuditLog.outcome)
            .where(AuditLog.tenant_id == tenant)
            .order_by(AuditLog.audit_id)
        ).all()


def error_text(result):
    assert result.is_error is True
    return result.content[0].text


# --- assign_module ---


async def test_assigning_creates_an_open_assignment_and_an_audit_row(database):
    result = await call(
        database, MARCOS, "assign_module", module_id="customer-returns", worker_id="ana.souza"
    )
    assert result.structured_content["created"] is True
    mine = await call(database, "acme/ana.souza", "my_assignments")
    assert "customer-returns" in [a["module_id"] for a in mine.structured_content["assignments"]]
    assert audit_trail(database)[-1] == (
        "assign_module",
        {"module_id": "customer-returns", "worker_id": "ana.souza"},
        "created",
    )


async def test_assigning_twice_returns_the_existing_one_with_a_note(database):
    args = {"module_id": "customer-returns", "worker_id": "ana.souza", "due_date": FUTURE}
    await call(database, MARCOS, "assign_module", **args)
    again = await call(database, MARCOS, "assign_module", **args)
    assert again.structured_content["created"] is False
    assert again.structured_content["note"] == f"Already assigned, due {FUTURE}."


async def test_a_different_due_date_is_never_applied_silently(database):
    args = {"module_id": "customer-returns", "worker_id": "ana.souza"}
    await call(database, MARCOS, "assign_module", **args, due_date=FUTURE)
    again = await call(database, MARCOS, "assign_module", **args, due_date="2099-12-31")
    assert again.structured_content["assignment"]["due_date"] == FUTURE
    assert "use change_due_date" in again.structured_content["note"]


async def test_simultaneous_assignments_create_exactly_one(database):
    """Check-then-insert would race here; the unique index makes the database the referee."""
    created = []

    async def assign():
        result = await call(
            database, MARCOS, "assign_module", module_id="customer-returns", worker_id="bea.costa"
        )
        created.append(result.structured_content["created"])

    async with anyio.create_task_group() as group:
        for _ in range(5):
            group.start_soon(assign)
    assert sorted(created) == [False, False, False, False, True]


async def test_a_completed_module_can_be_assigned_again(database):
    result = await call(
        database, MARCOS, "assign_module", module_id="food-handling-basics", worker_id="ana.souza"
    )
    assert result.structured_content["created"] is True  # recertification (D13)


@pytest.mark.parametrize("worker_id", ["rui.alves", "omar.haddad", "nobody"])
async def test_only_workers_on_your_team_can_be_assigned(database, worker_id):
    # rui.alves: same tenant, another team; omar.haddad: another tenant; nobody: doesn't exist.
    result = await call(
        database, MARCOS, "assign_module", module_id="customer-returns", worker_id=worker_id
    )
    assert error_text(result).endswith(f"No worker with ID {worker_id!r} on your team.")


async def test_workers_cannot_assign(database):
    result = await call(
        database,
        "acme/ana.souza",
        "assign_module",
        module_id="customer-returns",
        worker_id="bea.costa",
    )
    assert "permission" in error_text(result)


async def test_a_past_due_date_is_refused(database):
    result = await call(
        database,
        MARCOS,
        "assign_module",
        module_id="customer-returns",
        worker_id="ana.souza",
        due_date="2000-01-01",
    )
    assert "in the past" in error_text(result)


async def test_a_refused_write_leaves_no_trace(database):
    before = audit_trail(database)
    await call(
        database, MARCOS, "assign_module", module_id="customer-returns", worker_id="rui.alves"
    )
    assert audit_trail(database) == before  # the transaction rolled back: no row, no audit


# --- change_due_date ---


async def test_changing_a_due_date_reports_the_previous_one(database):
    result = await call(
        database,
        MARCOS,
        "change_due_date",
        module_id="forklift-safety",
        worker_id="ana.souza",
        due_date=FUTURE,
    )
    assert result.structured_content["assignment"]["due_date"] == FUTURE
    assert result.structured_content["previous_due_date"] is not None
    tool, target, outcome = audit_trail(database)[-1]
    assert (tool, target["to"], outcome) == ("change_due_date", FUTURE, "changed")


async def test_a_completed_assignment_has_no_due_date_to_change(database):
    result = await call(
        database,
        MARCOS,
        "change_due_date",
        module_id="food-handling-basics",
        worker_id="ana.souza",
        due_date=FUTURE,
    )
    assert "No open assignment" in error_text(result)


# --- draft_announcement ---


async def test_a_draft_stays_private(database):
    draft = await call(
        database, MARCOS, "draft_announcement", title="Inventory day", body="Count starts at 7."
    )
    assert draft.structured_content["team_size"] == 3
    seen = await call(database, "acme/ana.souza", "list_announcements")
    titles = [a["title"] for a in seen.structured_content["announcements"]]
    assert "Inventory day" not in titles
    assert audit_trail(database)[-1][0] == "draft_announcement"


async def test_workers_cannot_draft(database):
    result = await call(
        database, "acme/ana.souza", "draft_announcement", title="Hi", body="Hello team"
    )
    assert "permission" in error_text(result)
