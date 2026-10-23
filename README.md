# Frontline Training MCP

A production-shaped, multi-tenant [Model Context Protocol](https://modelcontextprotocol.io) server for a fictional frontline-workforce training platform: OAuth 2.1, tenant isolation down to Postgres row-level security, deployed on Cloud Run, with tests, traces and an eval scorecard.

> **Status: early skeleton.** One stub tool over fake data. Every company, person and module in this repo is fictional.

Built against MCP spec revision **2026-07-28** with the official Python SDK (`mcp` 2.x).

## Run locally

```bash
uv sync
uv run frontline-training-mcp          # serves http://127.0.0.1:8080/mcp (override with PORT)
npx @modelcontextprotocol/inspector    # connect with transport "Streamable HTTP"
```

## Local database

Postgres 17 runs in Docker on `127.0.0.1:55432`, so it can't clash with a local install on 5432.

```bash
cp .env.example .env                       # local-only credentials (gitignored): change the password
docker compose up -d --wait
uv run --env-file .env ftm-migrate --seed  # schema + fake demo data (dev only)
```

Migrations are numbered plain-SQL files in `migrations/`. Each is applied once and checksummed, so an applied file must never change: add a new one instead.

## Test

```bash
uv run --env-file .env pytest   # database tests run only when DATABASE_URL is set
uv run ruff check && uv run ruff format --check
```
