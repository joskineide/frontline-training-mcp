"""The platform stand-in (D14): its HTTP surface, its security layer and the publish step."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient
from support import requires_db, seeded_database

from frontline_training_mcp import service
from frontline_training_mcp.identity import Identity, lookup_identity
from frontline_training_mcp.models import AuditLog
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
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_a_foreign_host_is_refused(client):
    # DNS rebinding: a malicious site's name resolving to 127.0.0.1 still carries its own Host.
    response = client.get("/api/me", headers={"host": "evil.example", "X-Dev-User": ANA})
    assert response.status_code == 400


def test_without_a_viewer_nothing_is_shown(client):
    assert get(client, "/api/me").status_code == 401


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


# --- Publishing: the human step (D12) ---


def test_a_manager_publishes_their_draft_and_the_team_sees_it(client, database):
    response = publish(client, holiday_draft(client), MARCOS)
    assert response.status_code == 200
    assert response.json()["recipients"] == 3
    assert "Holiday schedule" in titles(get(client, "/api/me/announcements", ANA))
    # The MCP tools read through the same service: a worker's assistant now sees it too.
    ana = lookup_identity(database, "acme", "ana.souza")
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
    ("who", "status"),
    [(PAULA, 404), (LI, 404), (ANA, 403)],  # another team, another tenant, a worker
)
def test_nobody_else_can_publish_the_draft(client, who, status):
    assert publish(client, holiday_draft(client), who).status_code == status


def test_an_mcp_identity_can_never_publish(client, database):
    draft_id = holiday_draft(client)
    marcos = lookup_identity(database, "acme", "marcos.reis")  # the MCP audience
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
