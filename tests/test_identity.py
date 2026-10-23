"""The development stand-in for login must never be usable outside dev mode."""

import pytest
from support import requires_db, seeded_database

from frontline_training_mcp.db import Database
from frontline_training_mcp.identity import dev_identity

UNUSED_DB = Database("postgresql://unused@127.0.0.1:1/unused")  # never connected to here


def test_the_dev_identity_refuses_outside_dev_mode(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("DEV_USER", "acme/ana.souza")
    with pytest.raises(RuntimeError, match="only exists with APP_ENV=dev"):
        dev_identity(UNUSED_DB)


def test_the_dev_identity_refuses_when_app_env_is_missing(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)  # fail closed: no setting is not "dev"
    monkeypatch.setenv("DEV_USER", "acme/ana.souza")
    with pytest.raises(RuntimeError):
        dev_identity(UNUSED_DB)


def test_the_dev_identity_needs_a_tenant_and_a_user(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DEV_USER", "ana.souza")
    with pytest.raises(RuntimeError, match="DEV_USER=<tenant>/<user>"):
        dev_identity(UNUSED_DB)


@requires_db
def test_the_mcp_entry_point_yields_the_mcp_audience(monkeypatch):
    """Pins the first publish lock at its source: MCP callers are never platform callers."""
    monkeypatch.setenv("APP_ENV", "dev")
    with seeded_database() as db:
        monkeypatch.setenv("DEV_USER", "acme/marcos.reis")  # a manager
        me = dev_identity(db)
    assert me.audience == "mcp"
    assert "announcements:publish" not in me.scopes
