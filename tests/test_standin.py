"""The platform stand-in (D14): its HTTP surface, its security layer and the publish step."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, update
from starlette.testclient import TestClient
from support import requires_db, seeded_database

from frontline_training_mcp import service
from frontline_training_mcp.identity import Identity, lookup_identity
from frontline_training_mcp.models import Announcement, AuditLog
from frontline_training_mcp.standin.app import create_app

pytestmark = requires_db

ANA = "acme/ana.souza"  # worker, Store 12
MARCOS = "acme/marcos.reis"  # manager, Store 12: authored the seeded draft "Holiday schedule"
PAULA = "acme/paula.mendes"  # manager, Store 7
LI = "globex/li.wei"  # manager, another tenant


@pytest.fixture
def database(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    with seeded_database() as database:
        yield database


@pytest.fixture
def client(database):
    return TestClient(create_app(database), base_url="http://127.0.0.1:8081")


def get(client, path, who=None):
    return client.get(path, headers={"X-Dev-User": who} if who else {})


def publish(client, draft_id, who):
    return client.post(f"/api/drafts/{draft_id}/publish", headers={"X-Dev-User": who})


def holiday_draft(client) -> int:
    drafts = get(client, "/api/me/drafts", MARCOS).json()["drafts"]
    return next(d["draft_id"] for d in drafts if d["title"] == "Holiday schedule")


def titles(response):
    return [a["title"] for a in response.json()["announcements"]]


# --- The browser-facing layer ---


def test_the_page_is_labeled_and_protected(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "AI-generated UI" in response.text
    policy = response.headers["content-security-policy"]
    assert "script-src 'self'" in policy
    assert "require-trusted-types-for 'script'" in policy  # the browser refuses HTML sinks
    assert response.headers["x-content-type-options"] == "nosniff"


def test_a_foreign_host_is_refused(client):
    # DNS rebinding: a malicious site's name resolving to 127.0.0.1 still carries its own Host.
    response = client.get("/api/me", headers={"host": "evil.example", "X-Dev-User": ANA})
    assert response.status_code == 400
    assert "content-security-policy" in response.headers  # even Starlette's own refusals


@pytest.mark.parametrize(
    "viewer", [None, "acme", "acme/", "/ana.souza", "acme/nobody", "nope/ana.souza", "acme/li.wei"]
)
def test_without_a_known_viewer_nothing_is_shown(client, viewer):
    assert get(client, "/api/me", viewer).status_code == 401


def test_outside_dev_mode_nothing_works(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    assert get(client, "/api/me", ANA).status_code == 401
    assert get(client, "/api/dev/users").status_code == 404


def test_the_view_as_directory_lists_both_tenants(client):
    users = get(client, "/api/dev/users").json()["users"]
    assert {user["tenant_id"] for user in users} == {"acme", "globex"}
    assert len(users) == 9


# --- What each user sees ---


def test_a_worker_sees_their_training_and_their_teams_announcements(client):
    assert get(client, "/api/me", ANA).json()["role"] == "worker"
    assert len(get(client, "/api/me/assignments", ANA).json()["assignments"]) == 4
    assert titles(get(client, "/api/me/announcements", ANA)) == ["Store opens late on Monday"]


def test_a_worker_has_no_team_view_and_no_drafts(client):
    assert get(client, "/api/me/team", ANA).status_code == 403
    assert get(client, "/api/me/drafts", ANA).status_code == 403


def test_a_manager_sees_their_team_and_drafts(client):
    workers = get(client, "/api/me/team", MARCOS).json()["workers"]
    assert [w["worker_id"] for w in workers] == ["ana.souza", "bea.costa", "joao.lima"]
    assert all(w["module_status"] is None for w in workers)
    drafts = get(client, "/api/me/drafts", MARCOS).json()
    assert [d["title"] for d in drafts["drafts"]] == ["Holiday schedule"]
    assert drafts["team_size"] == 3


# --- Publishing: the human step (D12) ---


def test_a_manager_publishes_their_draft_and_the_team_sees_it(client, database):
    response = publish(client, holiday_draft(client), MARCOS)
    assert response.status_code == 200
    assert response.json()["recipients"] == 3
    assert "Holiday schedule" in titles(get(client, "/api/me/announcements", ANA))
    # The MCP tools read through the same service: a worker's assistant now sees it too.
    ana = lookup_identity(database, "acme", "ana.souza", audience="mcp")
    via_mcp = service.list_announcements(database, ana, 10, None)
    assert "Holiday schedule" in [a.title for a in via_mcp.announcements]
    with database.tenant_session("acme", read_only=True) as session:
        last = session.execute(
            select(AuditLog.tool, AuditLog.outcome)
            .where(AuditLog.tenant_id == "acme")
            .order_by(AuditLog.audit_id.desc())
        ).first()
    assert tuple(last) == ("platform:publish_draft", "published")


def test_publishing_twice_is_a_conflict(client):
    draft_id = holiday_draft(client)
    assert publish(client, draft_id, MARCOS).status_code == 200
    assert publish(client, draft_id, MARCOS).status_code == 409


@pytest.mark.parametrize(
    "draft_id", [0, 999_999, 2**63, 10**30, "1" * 5000, "-1", "abc", "1.5", "00000000000000000001"]
)
def test_unknown_or_impossible_draft_ids_get_the_same_404(client, draft_id):
    response = publish(client, draft_id, MARCOS)
    assert response.status_code == 404
    assert response.json() == {"error": "No such draft."}


def test_more_drafts_than_shown_are_signalled(client, database):
    marcos = lookup_identity(database, "acme", "marcos.reis", audience="mcp")
    for n in range(55):
        service.draft_announcement(database, marcos, f"Draft {n}", "Body")
    listed = get(client, "/api/me/drafts", MARCOS).json()
    assert len(listed["drafts"]) == 50 and listed["more"] is True
    assert "Holiday schedule" not in [d["title"] for d in listed["drafts"]]  # the oldest
    with database.tenant_session("acme", read_only=True) as session:
        oldest = session.scalar(
            select(Announcement.announcement_id).where(
                Announcement.tenant_id == "acme", Announcement.title == "Holiday schedule"
            )
        )
    assert publish(client, oldest, MARCOS).status_code == 200  # still publishable by ID


def test_an_unexpected_error_is_a_500_with_the_browser_protections(database, monkeypatch):
    def broken(*args):
        raise RuntimeError("a bug")

    monkeypatch.setattr(service, "profile", broken)
    client = TestClient(
        create_app(database), base_url="http://127.0.0.1:8081", raise_server_exceptions=False
    )
    response = get(client, "/api/me", ANA)
    assert response.status_code == 500
    assert "a bug" not in response.text  # no internals leak
    assert "content-security-policy" in response.headers


def test_drafts_list_only_what_can_be_published(client, database):
    """Listing and publishing apply the same rule, so the count shown is the real audience."""
    draft_id = holiday_draft(client)
    with database.tenant_session("acme", read_only=False) as session:
        session.execute(
            update(Announcement)
            .where(Announcement.tenant_id == "acme", Announcement.announcement_id == draft_id)
            .values(team_id="store-7")  # now another team's draft
        )
    assert get(client, "/api/me/drafts", MARCOS).json()["drafts"] == []
    assert publish(client, draft_id, MARCOS).status_code == 404


def test_any_expected_failure_keeps_its_message(client, monkeypatch):
    """A ServiceError type the stand-in has never heard of still answers in JSON, not a 500."""

    def refuse(*args):
        raise service.ServiceError("Try again later.")

    monkeypatch.setattr(service, "my_drafts", refuse)
    response = get(client, "/api/me/drafts", MARCOS)
    assert response.status_code == 400
    assert response.json() == {"error": "Try again later."}
    assert "content-security-policy" in response.headers


@pytest.mark.parametrize(
    ("who", "status"),
    [(PAULA, 404), (LI, 404), (ANA, 403)],  # another team, another tenant, a worker
)
def test_nobody_else_can_publish_the_draft(client, who, status):
    assert publish(client, holiday_draft(client), who).status_code == status


def test_an_mcp_identity_can_never_publish(client, database):
    draft_id = holiday_draft(client)
    marcos = lookup_identity(database, "acme", "marcos.reis", audience="mcp")
    assert "announcements:publish" not in marcos.scopes
    with pytest.raises(service.Forbidden):
        service.publish_draft(database, marcos, draft_id)
    # The second lock: even an MCP identity wrongly given the scope is refused.
    forged = Identity(
        tenant_id="acme",
        user_id="marcos.reis",
        scopes=marcos.scopes | {"announcements:publish"},
        audience="mcp",
    )
    with pytest.raises(service.Forbidden):
        service.publish_draft(database, forged, draft_id)


def test_simultaneous_publishes_have_exactly_one_winner(client, database):
    draft_id = holiday_draft(client)
    marcos = lookup_identity(database, "acme", "marcos.reis", audience="platform")

    def attempt(_):
        try:
            service.publish_draft(database, marcos, draft_id)
            return "published"
        except service.Conflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = sorted(pool.map(attempt, range(4)))
    assert outcomes == ["conflict", "conflict", "conflict", "published"]
