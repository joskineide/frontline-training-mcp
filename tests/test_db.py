"""The ORM mapping and the tenant-scoped session helper, against a real Postgres."""

import psycopg
import pytest
from sqlalchemy import MetaData, insert, select, text
from sqlalchemy.exc import DBAPIError
from support import DATABASE_URL, fresh_database, requires_db

from frontline_training_mcp.db import Database
from frontline_training_mcp.models import Base, Tenant

pytestmark = requires_db


@pytest.fixture(scope="module")
def db_url():
    with fresh_database() as url:
        yield url


@pytest.fixture(scope="module")
def database(db_url):
    # One pooled connection only, so consecutive transactions provably reuse it.
    database = Database(db_url, pool_size=1, max_overflow=0)
    yield database
    database.dispose()


def test_models_match_the_migrated_schema(database):
    """The migrations are the source of truth; the ORM mapping must not drift from them."""
    actual = MetaData()
    actual.reflect(database.engine)
    dialect = database.engine.dialect
    drift = []
    for model in Base.metadata.sorted_tables:
        table = actual.tables[model.name]
        if set(model.columns.keys()) != set(table.columns.keys()):
            drift.append(f"{model.name}: columns {model.columns.keys()} vs {table.columns.keys()}")
            continue
        for column in model.columns:
            real = table.columns[column.name]
            if column.type.compile(dialect) != real.type.compile(dialect):
                drift.append(f"{model.name}.{column.name}: type {column.type} vs {real.type}")
            if column.nullable != real.nullable:
                drift.append(f"{model.name}.{column.name}: nullable {column.nullable}")
        if [c.name for c in model.primary_key] != [c.name for c in table.primary_key]:
            drift.append(f"{model.name}: primary key differs")
    assert drift == []


def test_no_relationship_can_load_lazily():
    """Guardrail for later: any relationship() added must use lazy="raise"."""
    lazy = [
        f"{mapper.class_.__name__}.{rel.key}"
        for mapper in Base.registry.mappers
        for rel in mapper.relationships
        if rel.lazy != "raise"
    ]
    assert lazy == []


def test_the_session_is_scoped_to_its_tenant(database):
    with database.tenant_session("acme", read_only=True) as session:
        assert session.scalar(text("SELECT current_setting('app.tenant_id')")) == "acme"


def test_the_tenant_setting_ends_with_the_transaction(database):
    with database.tenant_session("acme", read_only=True):
        pass
    # Same physical connection (pool of one), next request: the setting must be gone.
    with database.engine.connect() as conn:
        leftover = conn.scalar(text("SELECT current_setting('app.tenant_id', true)"))
    assert leftover in (None, "")


def test_a_read_only_session_refuses_writes(database):
    with pytest.raises(DBAPIError) as raised:
        with database.tenant_session("acme", read_only=True) as session:
            session.execute(insert(Tenant).values(tenant_id="acme", name="Acme"))
    assert isinstance(raised.value.orig, psycopg.errors.ReadOnlySqlTransaction)


def test_a_write_session_commits(database):
    with database.tenant_session("acme", read_only=False) as session:
        session.execute(insert(Tenant).values(tenant_id="acme", name="Acme Retail"))
    with database.tenant_session("acme", read_only=True) as session:
        assert (
            session.scalar(select(Tenant.name).where(Tenant.tenant_id == "acme")) == "Acme Retail"
        )


def test_a_missing_tenant_is_refused(database):
    with pytest.raises(ValueError, match="tenant is required"):
        with database.tenant_session("", read_only=True):
            pass


def connect_timeout(database):
    with database.engine.connect() as conn:
        return conn.connection.driver_connection.info.get_parameters().get("connect_timeout")


def test_connections_give_up_after_five_seconds_by_default():
    """psycopg would otherwise wait 130 s for a database that doesn't answer."""
    database = Database(DATABASE_URL)
    try:
        assert connect_timeout(database) == "5"
    finally:
        database.dispose()


def test_a_connect_timeout_in_the_url_wins():
    database = Database(f"{DATABASE_URL}?connect_timeout=30")
    try:
        assert connect_timeout(database) == "30"
    finally:
        database.dispose()
