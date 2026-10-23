# Frontline Training MCP

A production-shaped, multi-tenant [Model Context Protocol](https://modelcontextprotocol.io) server for a fictional frontline-workforce training platform: OAuth 2.1, tenant isolation down to Postgres row-level security, deployed on Cloud Run, with tests, traces and an eval scorecard.

> **Status: early development.** Eight MCP tools (five read, three write) over a real Postgres, plus a development-only platform stand-in. There is no login yet: dev-only stand-ins play the caller (`DEV_USER` for MCP, a header for the stand-in). Every company, person and module in this repo is fictional.

Built against MCP spec revision **2026-07-28** with the official Python SDK (`mcp` 2.x).

## Local database

Postgres 17 runs in Docker on `127.0.0.1:55432`, so it can't clash with a local install on 5432.

```bash
uv sync
cp .env.example .env                       # local-only credentials (gitignored): change the password
docker compose up -d --wait
uv run --env-file .env ftm-migrate --seed  # schema + fake demo data (dev only)
```

Migrations are numbered plain-SQL files in `migrations/`. Each is applied once and checksummed, so an applied file must never change: add a new one instead.

## Run the server

```bash
uv run --env-file .env frontline-training-mcp   # http://127.0.0.1:8080/mcp, acting as DEV_USER
npx @modelcontextprotocol/inspector             # connect with transport "Streamable HTTP"
```

## Platform stand-in (development only)

A small web page that shows what each user sees, and is where a person publishes the drafts an assistant prepared: the AI can only draft (see `docs/design.md`, D12 and D14). **Its UI is AI-generated and labeled as such**; its security layer is the same service code the MCP tools use.

```bash
uv run --env-file .env ftm-standin   # http://127.0.0.1:8081 (STANDIN_PORT to change)
```

Pick a user under "View as". It only runs with `APP_ENV=dev`, binds to 127.0.0.1, and will switch to real login, with tokens issued for the platform, never MCP tokens.

## Test

```bash
uv run --env-file .env pytest   # database tests run only when DATABASE_URL is set
uv run ruff check && uv run ruff format --check
```
