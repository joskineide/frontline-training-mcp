"""Shared helpers for tests that need a real Postgres (see "Local database" in the README)."""

import os
import uuid
from contextlib import contextmanager

import psycopg
import pytest
from mcp import Client
from psycopg import sql
from sqlalchemy.engine import make_url

from frontline_training_mcp.db import Database
from frontline_training_mcp.identity import lookup_identity
from frontline_training_mcp.migrate import apply_dev_seed, apply_migrations
from frontline_training_mcp.server import create_server

DATABASE_URL = os.environ.get("DATABASE_URL", "")
# Skip database tests only on a developer machine without a database. In CI (GitHub sets CI=true)
# they must run: a missing DATABASE_URL there fails loudly instead of skipping to a green build.
requires_db = pytest.mark.skipif(
    not DATABASE_URL and os.environ.get("CI") != "true", reason="DATABASE_URL is not set"
)


def _with_database(url: str, name: str) -> str:
    return make_url(url).set(database=name).render_as_string(hide_password=False)


@contextmanager
def fresh_database():
    """A throwaway database with every migration applied; dropped afterwards. Yields its URL."""
    name = f"ftm_test_{uuid.uuid4().hex[:8]}"
    admin = _with_database(DATABASE_URL, "postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = _with_database(DATABASE_URL, name)
    try:
        apply_migrations(url)
        yield url
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@contextmanager
def seeded_database():
    """A throwaway database with the dev seed loaded. Yields a Database."""
    with fresh_database() as url, pytest.MonkeyPatch.context() as env:
        env.setenv("APP_ENV", "dev")
        apply_dev_seed(url)
        database = Database(url)
        try:
            yield database
        finally:
            database.dispose()


async def call(database: Database, caller: str, tool: str, **arguments):
    """Call a tool as `tenant/user`, through a real MCP client."""
    tenant, user = caller.split("/")
    server = create_server(
        database, lambda: lookup_identity(database, tenant, user, audience="mcp")
    )
    async with Client(server) as client:
        return await client.call_tool(tool, arguments)
