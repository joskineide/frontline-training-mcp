"""Rules the database enforces by itself. Needs a Postgres: see "Local database" in the README."""

import psycopg
import pytest
from psycopg import errors
from support import fresh_database, requires_db

from frontline_training_mcp.migrate import MigrationError, apply_dev_seed, apply_migrations

pytestmark = requires_db


@pytest.fixture(scope="module")
def db():
    with fresh_database() as url:
        yield url


@pytest.fixture
def conn(db):
    """Two tenants with the same module slug, rolled back after each test."""
    with psycopg.connect(db) as conn:
        conn.execute("""
            INSERT INTO tenant VALUES ('acme', 'Acme Retail'), ('globex', 'Globex Logistics');
            INSERT INTO team VALUES ('acme', 'store-12', 'Store 12', 'marcos'),
                                    ('globex', 'north', 'North', 'li');
            INSERT INTO app_user (tenant_id, user_id, team_id, display_name, role) VALUES
                ('acme', 'marcos', NULL, 'Marcos', 'manager'),
                ('acme', 'ana', 'store-12', 'Ana', 'worker'),
                ('globex', 'li', NULL, 'Li', 'manager');
            INSERT INTO module (tenant_id, module_id, title, summary, content, duration_minutes)
            VALUES ('acme', 'forklift-safety', 'Forklift Safety', 's', 'c', 25),
                   ('globex', 'forklift-safety', 'Forklift Operation', 's', 'c', 30),
                   ('globex', 'hazmat-labels', 'Hazmat Labels', 's', 'c', 20);
        """)
        yield conn
        conn.rollback()


def assign(conn, module_id, completed=False):
    conn.execute(
        "INSERT INTO assignment (tenant_id, worker_id, module_id, assigned_by, completed_at)"
        " VALUES ('acme', 'ana', %s, 'marcos', CASE WHEN %s THEN now() END)",
        (module_id, completed),
    )


def test_migrations_apply_once(db):
    assert apply_migrations(db) == []


def test_an_edited_migration_is_refused(db, tmp_path):
    (tmp_path / "001_schema.sql").write_text("-- edited after being applied", encoding="utf-8")
    with pytest.raises(MigrationError, match="changed after it was applied"):
        apply_migrations(db, tmp_path)


def test_only_one_open_assignment_per_worker_and_module(conn):
    assign(conn, "forklift-safety")
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        assign(conn, "forklift-safety")


def test_a_completed_module_can_be_assigned_again(conn):
    assign(conn, "forklift-safety", completed=True)
    assign(conn, "forklift-safety")  # recertification (D13)
    count = conn.execute("SELECT count(*) FROM assignment").fetchone()[0]
    assert count == 2


def test_an_assignment_cannot_reach_another_tenants_module(conn):
    # hazmat-labels exists only in globex; an acme assignment must not point at it.
    with pytest.raises(errors.ForeignKeyViolation), conn.transaction():
        assign(conn, "hazmat-labels")


def test_a_published_announcement_needs_a_publisher(conn):
    with pytest.raises(errors.CheckViolation), conn.transaction():
        conn.execute(
            "INSERT INTO announcement (tenant_id, team_id, author_id, title, body, status)"
            " VALUES ('acme', 'store-12', 'marcos', 'Hi', 'Hello', 'published')"
        )


def test_the_seed_refuses_to_run_outside_dev_mode(db, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(MigrationError, match="APP_ENV=dev"):
        apply_dev_seed(db)


def test_the_seed_loads_two_tenants(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    with fresh_database() as url:
        apply_dev_seed(url)
        with psycopg.connect(url) as conn:
            tenants = conn.execute("SELECT tenant_id FROM tenant ORDER BY 1").fetchall()
    assert tenants == [("acme",), ("globex",)]


def test_a_manager_leads_at_most_one_team(conn):
    with pytest.raises(errors.UniqueViolation), conn.transaction():
        conn.execute("INSERT INTO team VALUES ('acme', 'store-99', 'Store 99', 'marcos')")
