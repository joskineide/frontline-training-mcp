# Frontline Training MCP

A production-shaped, multi-tenant [Model Context Protocol](https://modelcontextprotocol.io) server for a fictional frontline-workforce training platform: OAuth 2.1, tenant isolation down to Postgres row-level security, deployed on Cloud Run, with tests, traces and an eval scorecard.

> **Status: Phase 0 (skeleton).** One stub tool over fake data. Every company, person and module in this repo is fictional.

Built against MCP spec revision **2026-07-28** with the official Python SDK (`mcp` 2.x).

## Run locally

```bash
uv sync
uv run frontline-training-mcp          # serves http://127.0.0.1:8080/mcp (override with PORT)
npx @modelcontextprotocol/inspector    # connect with transport "Streamable HTTP"
```

## Test

```bash
uv run pytest
uv run ruff check && uv run ruff format --check
```
