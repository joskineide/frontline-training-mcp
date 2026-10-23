"""Apply numbered SQL migrations once each, in order: a tiny Flyway.

Usage: ftm-migrate [--seed]. Reads DATABASE_URL; --seed also requires APP_ENV=dev.
"""

import argparse
import hashlib
import os
import sys
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = ROOT / "migrations"
DEV_SEED = ROOT / "seeds" / "dev_seed.sql"

# Any constant works, as long as every runner uses the same one: two runners can't interleave.
LOCK_ID = 7_302_811


class MigrationError(RuntimeError):
    pass


def _connect(conninfo: str, **options) -> psycopg.Connection:
    # Fail in seconds, not psycopg's 130-second default, unless the URL sets its own timeout.
    if "connect_timeout" not in conninfo_to_dict(conninfo):
        options["connect_timeout"] = 5
    return psycopg.connect(conninfo, **options)


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def apply_migrations(conninfo: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations; refuse to run if an already-applied file was edited."""
    applied_now: list[str] = []
    with _connect(conninfo, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migration ("
            " version text PRIMARY KEY,"
            " checksum text NOT NULL,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )
        applied = dict(conn.execute("SELECT version, checksum FROM schema_migration").fetchall())
        for path in sorted(directory.glob("*.sql")):
            sql = path.read_text(encoding="utf-8")
            if path.name in applied:
                if applied[path.name] != _checksum(sql):
                    raise MigrationError(
                        f"{path.name} changed after it was applied; add a new migration instead."
                    )
                continue
            with conn.transaction():  # each file applies completely or not at all
                conn.execute(sql)
                conn.execute(
                    "INSERT INTO schema_migration (version, checksum) VALUES (%s, %s)",
                    (path.name, _checksum(sql)),
                )
            applied_now.append(path.name)
    return applied_now


def apply_dev_seed(conninfo: str, seed: Path = DEV_SEED) -> None:
    """Load the fake demo data. It replaces all tenant data, so it only runs in dev mode."""
    if os.environ.get("APP_ENV") != "dev":
        raise MigrationError("The seed replaces all data; it only runs with APP_ENV=dev.")
    with _connect(conninfo) as conn:  # one transaction, committed on exit
        conn.execute(seed.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply database migrations.")
    parser.add_argument("--seed", action="store_true", help="also load fake demo data (dev only)")
    args = parser.parse_args()
    conninfo = os.environ.get("DATABASE_URL")
    if not conninfo:
        sys.exit("DATABASE_URL is not set.")
    try:
        applied = apply_migrations(conninfo)
        print(f"Applied: {', '.join(applied)}" if applied else "Schema is up to date.")
        if args.seed:
            apply_dev_seed(conninfo)
            print("Dev seed loaded.")
    except MigrationError as exc:
        sys.exit(str(exc))
    except psycopg.OperationalError as exc:
        sys.exit(f"Cannot reach the database: {exc}")


if __name__ == "__main__":
    main()
