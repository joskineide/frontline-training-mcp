"""Database access: one engine per process, one short session per tenant-scoped transaction.

Synchronous on purpose (design decision D15). The MCP SDK runs synchronous tools in a
worker thread pool, so a blocking query never stalls the event loop.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


def sqlalchemy_url(database_url: str) -> str:
    """postgresql://... → postgresql+psycopg://... (SQLAlchemy needs the driver named)."""
    url = make_url(database_url).set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)


class Database:
    def __init__(self, database_url: str, **engine_options: Any) -> None:
        # pool_pre_ping checks a pooled connection before handing it out: a hosted Postgres
        # that scales to zero (Neon's free tier) drops idle connections.
        self.engine: Engine = create_engine(
            sqlalchemy_url(database_url), pool_pre_ping=True, **engine_options
        )
        # Results must be turned into plain values inside the transaction; nothing reloads after.
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def tenant_session(self, tenant_id: str, *, read_only: bool) -> Iterator[Session]:
        """A fresh session inside one transaction scoped to `tenant_id`, committed on success.

        `read_only` has no default on purpose: every caller decides explicitly (D7).
        """
        if not tenant_id:
            raise ValueError("A tenant is required.")  # fail closed: never run unscoped
        with self._sessions() as session, session.begin():
            if read_only:
                # Set first, so every statement of the transaction runs read-only.
                session.execute(text("SET TRANSACTION READ ONLY"))
            # set_config(..., is_local => true) is SET LOCAL that accepts a bound parameter
            # (SET LOCAL itself can't), and the value ends with the transaction, so a pooled
            # connection can never carry one tenant's setting into another request.
            session.execute(
                text("SELECT set_config('app.tenant_id', :tenant, true)"), {"tenant": tenant_id}
            )
            yield session

    def dispose(self) -> None:
        self.engine.dispose()
