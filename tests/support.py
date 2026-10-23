"""Shared helpers for tests that need a real Postgres (see "Local database" in the README)."""

import os
import uuid
from contextlib import contextmanager

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from frontline_training_mcp.migrate import apply_migrations

DATABASE_URL = os.environ.get("DATABASE_URL", "")
requires_db = pytest.mark.skipif(not DATABASE_URL, reason="DATABASE_URL is not set")


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
