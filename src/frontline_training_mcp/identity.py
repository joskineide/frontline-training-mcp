"""Who is calling: the tenant, the user and their scopes.

Every tool asks for the caller's Identity and never accepts one as an argument. Today the
identity comes from a development stand-in (DEV_USER); verified access tokens replace that
source, and the tools don't change.
"""

import os
from dataclasses import dataclass

from sqlalchemy import select

from frontline_training_mcp.db import Database
from frontline_training_mcp.models import AppUser

# Roles are bundles of scopes (docs/design.md §4). The identity provider will grant these.
ROLE_SCOPES: dict[str, frozenset[str]] = {
    "worker": frozenset({"training:read"}),
    "manager": frozenset(
        {"training:read", "team:read", "assignments:write", "announcements:draft"}
    ),
}


@dataclass(frozen=True)
class Identity:
    tenant_id: str
    user_id: str
    scopes: frozenset[str]


def lookup_identity(db: Database, tenant_id: str, user_id: str) -> Identity:
    """Resolve a known user to an Identity, with the scopes of their role."""
    with db.tenant_session(tenant_id, read_only=True) as session:
        role = session.scalar(
            select(AppUser.role).where(AppUser.tenant_id == tenant_id, AppUser.user_id == user_id)
        )
    if role is None:
        raise LookupError(f"No user {user_id!r} in tenant {tenant_id!r}.")
    return Identity(tenant_id=tenant_id, user_id=user_id, scopes=ROLE_SCOPES[role])


def dev_identity(db: Database) -> Identity:
    """Development stand-in for login: DEV_USER=<tenant>/<user>. Refuses outside dev mode."""
    if os.environ.get("APP_ENV") != "dev":
        raise RuntimeError("The DEV_USER stand-in only exists with APP_ENV=dev.")
    tenant_id, _, user_id = os.environ.get("DEV_USER", "").partition("/")
    if not (tenant_id and user_id):
        raise RuntimeError("Set DEV_USER=<tenant>/<user>, for example DEV_USER=acme/ana.souza.")
    return lookup_identity(db, tenant_id, user_id)
