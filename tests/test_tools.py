"""The read tools against the seeded database, called through a real MCP client."""

import pytest
from support import call, requires_db, seeded_database

from frontline_training_mcp.identity import lookup_identity

pytestmark = [requires_db, pytest.mark.anyio]

ACME_MODULES = {
    "forklift-safety",
    "food-handling-basics",
    "cash-handling",
    "customer-returns",
    "fire-evacuation",
    "slip-and-fall-prevention",
    "store-opening-checklist",
    "store-closing-checklist",
}


@pytest.fixture(scope="module")
def database():
    """Read tools never write, so one seeded database serves the whole module."""
    with seeded_database() as database:
        yield database


def ids(result, key, field="module_id"):
    return [item[field] for item in result.structured_content[key]]


# --- Identity ---


def test_roles_map_to_scopes(database):
    assert lookup_identity(database, "acme", "ana.souza").scopes == {"training:read"}
    assert "team:read" in lookup_identity(database, "acme", "marcos.reis").scopes


def test_an_unknown_user_is_refused(database):
    with pytest.raises(LookupError):
        lookup_identity(database, "acme", "li.wei")  # exists, but in another tenant


# --- search_training ---


async def test_search_only_finds_your_tenants_modules(database):
    acme = await call(database, "acme/ana.souza", "search_training", query="forklift")
    globex = await call(database, "globex/omar.haddad", "search_training", query="forklift")
    assert ids(acme, "results") == ["forklift-safety"]
    assert acme.structured_content["results"][0]["title"] == "Forklift Safety"
    assert set(ids(globex, "results")) == {"forklift-safety", "loading-dock-safety"}


@pytest.mark.parametrize("query", ["forklift' OR 1=1 --", "a & !", "'; DROP TABLE module; --"])
async def test_search_treats_sql_and_operators_as_plain_text(database, query):
    result = await call(database, "acme/ana.souza", "search_training", query=query)
    assert result.is_error is False
    assert set(ids(result, "results")) <= ACME_MODULES


async def test_a_blank_search_is_refused(database):
    result = await call(database, "acme/ana.souza", "search_training", query="   ")
    assert result.is_error is True


# --- get_module ---


async def test_the_same_slug_resolves_within_your_own_tenant(database):
    acme = await call(database, "acme/ana.souza", "get_module", module_id="forklift-safety")
    globex = await call(database, "globex/omar.haddad", "get_module", module_id="forklift-safety")
    assert acme.structured_content["title"] == "Forklift Safety"
    assert globex.structured_content["title"] == "Warehouse Forklift Operation"


async def test_another_tenants_module_looks_exactly_like_a_missing_one(database):
    foreign = await call(database, "acme/ana.souza", "get_module", module_id="hazmat-labels")
    missing = await call(database, "acme/ana.souza", "get_module", module_id="no-such-module")
    assert foreign.is_error is True and missing.is_error is True
    assert foreign.content[0].text.replace("hazmat-labels", "X") == missing.content[0].text.replace(
        "no-such-module", "X"
    )


# --- my_assignments ---


async def test_my_assignments_lists_open_ones_by_default(database):
    result = await call(database, "acme/ana.souza", "my_assignments")
    items = {item["module_id"]: item for item in result.structured_content["assignments"]}
    assert set(items) == {"forklift-safety", "cash-handling", "fire-evacuation"}
    assert items["cash-handling"]["overdue"] is True
    assert items["forklift-safety"]["overdue"] is False


async def test_my_assignments_can_list_completed_ones(database):
    result = await call(database, "acme/ana.souza", "my_assignments", status="completed")
    assert ids(result, "assignments") == ["food-handling-basics"]


async def test_pages_follow_the_cursor(database):
    first = await call(database, "acme/ana.souza", "my_assignments", status="all", limit=2)
    cursor = first.structured_content["next_cursor"]
    second = await call(
        database, "acme/ana.souza", "my_assignments", status="all", limit=2, cursor=cursor
    )
    assert cursor is not None and second.structured_content["next_cursor"] is None
    all_ids = ids(first, "assignments") + ids(second, "assignments")
    assert len(all_ids) == len(set(all_ids)) == 4


async def test_a_tampered_cursor_is_refused(database):
    result = await call(database, "acme/ana.souza", "my_assignments", cursor="not-a-cursor")
    assert result.is_error is True


# --- list_announcements ---


@pytest.mark.parametrize(
    ("caller", "titles"),
    [
        ("acme/ana.souza", ["Store opens late on Monday"]),  # not the draft, not store 7's
        ("acme/marcos.reis", ["Store opens late on Monday"]),  # a manager reads the team he leads
        ("acme/rui.alves", ["New returns policy"]),
        ("globex/omar.haddad", ["Dock 3 closed for repairs"]),
    ],
)
async def test_announcements_are_published_ones_for_your_team(database, caller, titles):
    result = await call(database, caller, "list_announcements")
    assert ids(result, "announcements", field="title") == titles


# --- team_progress ---


async def test_team_progress_covers_only_your_team(database):
    result = await call(database, "acme/marcos.reis", "team_progress")
    workers = {worker["worker_id"]: worker for worker in result.structured_content["workers"]}
    assert set(workers) == {"ana.souza", "bea.costa", "joao.lima"}  # not rui.alves (store 7)
    ana = workers["ana.souza"]
    assert (ana["open"], ana["completed"], ana["overdue"]) == (3, 1, 1)


async def test_team_progress_for_one_module(database):
    result = await call(database, "acme/marcos.reis", "team_progress", module_id="forklift-safety")
    statuses = {w["worker_id"]: w["module_status"] for w in result.structured_content["workers"]}
    assert statuses == {"ana.souza": "open", "joao.lima": "completed", "bea.costa": "not_assigned"}


async def test_workers_cannot_see_team_progress(database):
    result = await call(database, "acme/ana.souza", "team_progress")
    assert result.is_error is True
    assert "permission" in result.content[0].text


async def test_team_progress_refuses_another_tenants_module(database):
    result = await call(database, "acme/marcos.reis", "team_progress", module_id="hazmat-labels")
    assert result.is_error is True
