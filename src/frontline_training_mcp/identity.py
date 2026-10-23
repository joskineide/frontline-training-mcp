"""Who is calling: the tenant, the user, their scopes, and which surface they came through.

Every tool or endpoint asks for the caller's Identity and never accepts one as an argument.
Today identities come from development stand-ins (DEV_USER for MCP, a header for the
platform stand-in). Verified access tokens replace those sources, one audience each, and
nothing downstream changes.
"""

import os
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select

from frontline_training_mcp.db import Database
from frontline_training_mcp.models import AppUser, Tenant

Audience = Literal["mcp", "platform"]

# Roles are bundles of scopes (docs/design.md §4). The identity provider will grant these
# to MCP clients. None of them can publish (D12).
ROLE_SCOPES: dict[str, frozenset[str]] = {
    "worker": frozenset({"training:read"}),
    "manager": frozenset(
        {"training:read", "team:read", "assignments:write", "announcements:draft"}
    ),
}
# Extra scopes that only exist for people using the platform itself (D14), never for MCP.
PLATFORM_SCOPES: dict[str, frozenset[str]] = {
    "manager": frozenset({"announcements:publish"}),
}


@dataclass(frozen=True)
class Identity:
    tenant_id: str
    user_id: str
    scopes: frozenset[str]
    audience: Audience = "mcp"


def lookup_identity(
    db: Database, tenant_id: str, user_id: str, audience: Audience = "mcp"
) -> Identity:
    """Resolve a known user to an Identity, with the scopes of their role for that audience."""
    with db.tenant_session(tenant_id, read_only=True) as session:
        role = session.scalar(
            select(AppUser.role).where(AppUser.tenant_id == tenant_id, AppUser.user_id == user_id)
        )
    if role is None:
        raise LookupError(f"No user {user_id!r} in tenant {tenant_id!r}.")
    scopes = ROLE_SCOPES[role]
    if audience == "platform":
        scopes = scopes | PLATFORM_SCOPES.get(role, frozenset())
    return Identity(tenant_id=tenant_id, user_id=user_id, scopes=scopes, audience=audience)


def require_dev_mode(what: str) -> None:
    if os.environ.get("APP_ENV") != "dev":
        raise RuntimeError(f"{what} only exists with APP_ENV=dev.")


def parse_dev_user(value: str) -> tuple[str, str]:
    tenant_id, _, user_id = value.partition("/")
    if not (tenant_id and user_id):
        raise RuntimeError("Use <tenant>/<user>, for example acme/ana.souza.")
    return tenant_id, user_id


def dev_identity(db: Database) -> Identity:
    """Development stand-in for login: DEV_USER=<tenant>/<user>. Refuses outside dev mode."""
    require_dev_mode("The DEV_USER stand-in")
    try:
        tenant_id, user_id = parse_dev_user(os.environ.get("DEV_USER", ""))
    except RuntimeError as exc:
        raise RuntimeError(f"Set DEV_USER=<tenant>/<user>. {exc}") from exc
    return lookup_identity(db, tenant_id, user_id)


def dev_directory(db: Database) -> list[dict[str, str]]:
    """Every user of every tenant, for the platform stand-in's "view as" picker. Dev only.

    The tenant list is the one cross-tenant read in the codebase, and it exists only for this
    development picker. Each tenant's users are then read inside that tenant's own session.
    """
    require_dev_mode("The user directory")
    with db.engine.connect() as conn:
        tenants = conn.execute(select(Tenant.tenant_id, Tenant.name).order_by(Tenant.name)).all()
    directory = []
    for tenant in tenants:
        with db.tenant_session(tenant.tenant_id, read_only=True) as session:
            users = session.execute(
                select(AppUser.user_id, AppUser.display_name, AppUser.role)
                .where(AppUser.tenant_id == tenant.tenant_id)
                .order_by(AppUser.role.desc(), AppUser.display_name)
            ).all()
        directory += [
            {
                "tenant_id": tenant.tenant_id,
                "tenant_name": tenant.name,
                "user_id": user.user_id,
                "name": user.display_name,
                "role": user.role,
            }
            for user in users
        ]
    return directory
