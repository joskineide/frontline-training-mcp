# Frontline Training MCP

![CI](https://github.com/joskineide/frontline-training-mcp/actions/workflows/ci.yml/badge.svg)

A multi-tenant [Model Context Protocol](https://modelcontextprotocol.io) server for a fictional frontline-workforce training platform. AI assistants use it to help workers find and track their training, and to help managers assign it, follow their team's progress and draft announcements, without ever stepping outside one company's data.

Built against MCP spec revision **2026-07-28** with the official Python SDK (`mcp` 2.3). A modern client calls tools with no handshake and no session, and older clients still work. Every company, person and module in this repo is fictional.

> **Status: work in progress, local only.**
> - **Built and tested:** eight MCP tools over Streamable HTTP, the Postgres data layer, application-level tenant scoping, race-safe idempotent writes with an audit row per write, and a development-only web app standing in for the training platform, where a person publishes drafts.
> - **No login yet, so no authentication.** The MCP server acts as one fixed user, read from `DEV_USER` at startup. It refuses to start unless `APP_ENV=dev`, and listens only on 127.0.0.1.
> - **Not yet driven end to end by an AI model.** Tests exercise the tools through the SDK's MCP client. A model-driven run is [Roadmap](#roadmap) step 4.
> - **Designed but not built yet:** login (OAuth 2.1), hiding tools a caller can't use (today they are listed, and refused when called), database-enforced isolation (row-level security), cloud deployment, rate limits, tracing, and the model eval.
>
> See [What's built](#whats-built) and the [Roadmap](#roadmap).

## The problem it solves
Frontline workers (cashiers, warehouse staff, drivers) need training, often mandatory, and their managers need to assign it, track it and communicate with their teams. Training platforms serve many customer companies (**tenants**) at once. This server is the layer meant to let an MCP-capable AI assistant work with that data **on a signed-in user's behalf, with exactly that user's permissions**. Sign-in isn't built yet: today the server acts as one configured user (see Status).

| Who | Asks their assistant | Tool used |
|---|---|---|
| Ana, worker | "What training do I still have to do?" | `my_assignments` |
| Ana | "How do I do the pre-shift forklift check?" | `search_training`, `get_module` |
| Ana | "Any news from my store?" | `list_announcements` |
| Marcos, manager | "Who on my team hasn't finished food safety?" | `team_progress` |
| Marcos | "Assign forklift safety to Ana, due Friday" | `assign_module` (and `change_due_date`) |
| Marcos | "Draft a note: the store opens late Monday" | `draft_announcement`: the AI drafts, **a person publishes** |

## What's built
- **Eight task-shaped MCP tools** (five read, three write) over Streamable HTTP. Their names, descriptions, input and output schemas and hints are written for the model. **A snapshot test fails on any change to that tool contract** until the snapshot is deliberately regenerated, so every change to what the model reads about a tool shows up as a reviewable diff ([`tests/snapshots/tools.json`](tests/snapshots/tools.json)). The server's `instructions` and its error messages are not snapshotted yet.
- **A shared service layer** ([`service.py`](src/frontline_training_mcp/service.py)) holding every business rule: scopes, team checks, tenant-scoped queries, writes and audit. The MCP tools and the platform stand-in are thin adapters over it (each adds only its own input validation and error mapping), so both enforce the same rules.
- **Tenant-scoped data access.** The tenant always comes from the caller's identity, never from a tool argument.
  - Every query on tenant data states its tenant filter.
  - Every transaction also sets the tenant (`set_config(..., true)`, scoped to that transaction; a test on a one-connection pool shows it never reaches the next request). Nothing reads that setting yet: it is the hook for the row-level security policies in Roadmap step 2.
  - Read tools run in read-only transactions, and a test shows a write inside one is refused.
  - Foreign keys include the tenant, so a cross-company link can't even be stored.
  - The one deliberate cross-tenant read is the stand-in's dev-only user picker, which lists the tenants themselves.
- **Race-safe, idempotent writes.**
  - Assigning a module that is still open returns the existing assignment with a note, and never changes its due date. This rests on a partial unique index and `INSERT … ON CONFLICT`, tested with five simultaneous calls, of which exactly one creates. The index is partial so that a completed module can be assigned again (recertification).
  - A repeated assign with a different due date is reported in a note, never applied silently. Changing a date is a separate, explicit tool (honestly marked destructive) that locks the row.
  - Every write and its audit row commit in the same transaction, and a refused call rolls back and leaves no row (recording refusals for alerting is Roadmap step 3). Audit rows hold IDs only, never free text such as a draft's body.
- **Results sized for a model's context.** Every list is capped and paged with an opaque cursor (a tampered one is refused). Module content is cut at 4,000 characters with a `truncated` flag. A draft reports how many people it would reach (`team_size`).
- **Human-in-the-loop publishing.** The AI can only *draft* announcements. Publishing happens in the platform stand-in. MCP has no publish tool or scope at all (a contract test checks this), and the service adds two independent locks: platform-only callers and a platform-only permission. Simultaneous publishes are tested to have exactly one winner, decided by a conditional `UPDATE`.
- **A platform stand-in** (development only): a small web page showing what each user sees, where a person publishes drafts.
  - Its browser UI is labeled "AI-generated" on the page because it is treated as disposable and untrusted. Instead of relying on review, a test forbids HTML-parsing sinks and inline script in it, and a strict CSP with Trusted Types makes the browser refuse them anyway.
  - Its security layer is the shared service code, plus Host-header checks against DNS rebinding and no CORS grants. The security headers are present even on error responses.
  - It binds to 127.0.0.1 and refuses to run outside `APP_ENV=dev`.
- **101 tests** (89 need Postgres; they use throwaway databases, no mocks).
  - 43 call the server through the SDK's in-memory MCP client: the tool contract and every read and write tool.
  - 36 cover the platform stand-in.
  - 22 cover the schema, migrations, the session helper and the dev identity.
  - They cover cross-company IDs getting the same answer as missing ones, SQL and search-operator text in inputs, and the tenant setting never outliving its transaction.
  - No test goes over HTTP yet. The wire captures (taken against the Phase 0 one-tool server) show the protocol on the wire.
  - CI runs them on every push to `main` and every pull request, with its own Postgres service, and fails rather than skipping if the database is missing.
- **Documentation of the reasoning:**
  - a [design doc](docs/design.md) with its decision log (D1–D15: 12 decided, 2 superseded, 1 open: the token claims, until an identity provider is chosen);
  - a [threat model](docs/threat-model.md) with 11 threats, mitigations, residual risk and evidence. Evidence exists today for:
    - T3: worker-on-manager and cross-team refusals.
    - T5: idempotency and race tests, one audit row per write. The insert-only audit grant is still to come.
    - T9: the lockfile is enforced in CI, and Actions are pinned to commit SHAs.
    - T10: for the search input.
    - T1: the application-layer half.

    The rest is planned. The dev stand-ins for login refuse to run unless `APP_ENV=dev`, and that is tested too;
  - [wire captures](docs/captures/README.md) comparing the legacy and the 2026-07-28 (stateless) protocol.

  Both documents describe the target system, in the present tense. What exists today is the Status list at the top. The threat model marks each row's evidence as planned until it lands.

## Architecture
```
 AI assistant (MCP host)                          a person, in a browser
          │ MCP over Streamable HTTP                       │ HTTP + JSON
          ▼                                                ▼
 ┌──────────────────────────────┐             ┌──────────────────────────┐
 │ MCP adapters                 │             │ Platform stand-in        │
 │ tools.py, write_tools.py     │             │ standin/ (dev only)      │
 │ descriptions, schemas, hints │             │ view as a user,          │
 │ ServiceError → ToolError     │             │ publish drafts           │
 └──────────────┬───────────────┘             └─────────────┬────────────┘
                └───────────────────┬───────────────────────┘
                                    ▼
                   ┌─────────────────────────────────┐
                   │ service.py: every rule, once    │
                   │ identity → scope → one tenant-  │
                   │ scoped transaction → compact    │
                   │ result (+ audit row on writes)  │
                   └────────────────┬────────────────┘
                                    ▼
                   ┌─────────────────────────────────┐
                   │ Postgres: tenant-keyed schema,  │
                   │ composite FKs, one-open-        │
                   │ assignment index, audit log     │
                   └─────────────────────────────────┘
```

## The tools
| Tool | Worker | Manager | Kind | Hints |
|---|:-:|:-:|---|---|
| `search_training` | ✓ | ✓ | read (full-text search) | read-only |
| `get_module` | ✓ | ✓ | read | read-only |
| `my_assignments` | ✓ | ✓ | read (self only) | read-only |
| `list_announcements` | ✓ | ✓ | read (own team, published only) | read-only |
| `team_progress` | | ✓ | read (own team only) | read-only |
| `assign_module` | | ✓ | write | idempotent |
| `change_due_date` | | ✓ | write | destructive (it overwrites a date), idempotent |
| `draft_announcement` | | ✓ | write (a private draft) | not idempotent (each call adds a draft) |

All eight tools declare themselves closed-world (`openWorldHint: false`), and a test pins every hint.

Access is decided by scopes that each role bundles ([design §4](docs/design.md#4-identity-scopes-and-roles)). Today every caller sees all eight tools. Calling one outside your scopes is refused with an error the model can read. Hiding those tools from `tools/list` comes with login (Roadmap step 1). Other expected failures also come back as results the model can read and act on. "Doesn't exist" and "belongs to someone else" give the same message and shape, so an error never confirms that another team's or company's record exists. Timing differences remain a documented residual risk (threat model T1).

## Key decisions, and why
The full list, with the reasoning for each, is in [docs/design.md](docs/design.md#9-decisions).
- **Task-shaped tools, not CRUD.** Every extra tool costs the model context and adds a choice, and every write tool is attack surface for injected instructions.
- **Identity is never an argument.** No tool accepts a tenant or a user ID for "me", so there is nothing to tamper with.
- **The AI prepares; people commit what's irreversible.**
  - Small, contained writes (assigning a module, moving a due date) are left to the AI, behind the host's approval prompt.
  - That prompt is the host's job, not this server's, so the server enforces scopes and team checks regardless.
  - A broadcast to a whole team is only drafted.
- **The database is the referee for races.** A check in code followed by an insert would race, so idempotency rests on a unique index, and publishing on a conditional `UPDATE`.
- **Explicit data access.** No ORM relationships (nothing loads implicitly outside its transaction), one short session per tenant transaction, and the tenant filter written in every query.
- **Synchronous database access.** The SDK runs synchronous tools in a worker thread pool, so a query never blocks the event loop. There's no measured need for async, and psycopg's async mode refuses Windows' default event loop, so sync keeps local runs and CI identical.
- **Plain-SQL migrations,** applied once, checksummed and run under a lock. A test fails if the ORM mapping's columns, types, nullability or primary keys drift from them.

## Run it locally
Requires Python 3.13, [uv](https://docs.astral.sh/uv/) and Docker (plus Node.js, only for the optional MCP Inspector).

### Local database
```bash
uv sync
cp .env.example .env    # (Windows cmd: copy) local-only, gitignored: set the same new password in
                        # POSTGRES_PASSWORD and DATABASE_URL, before the first `docker compose up`
docker compose up -d --wait                 # Postgres 17 on 127.0.0.1:55432
uv run --env-file .env ftm-migrate --seed   # schema + fake demo data (dev only)
```

### The servers
```bash
uv run --env-file .env frontline-training-mcp   # MCP server on http://127.0.0.1:8080/mcp (PORT to change), acting as DEV_USER
uv run --env-file .env ftm-standin              # platform stand-in on http://127.0.0.1:8081 (STANDIN_PORT to change)
```

Explore the tools with the MCP Inspector, pinned like every other tool:

```bash
npx -y @modelcontextprotocol/inspector@2.10.1 --transport http --server-url http://127.0.0.1:8080/mcp --protocol-era modern
```

Without `--protocol-era modern` it speaks the legacy protocol, with a session. The server reads `DEV_USER` once at startup. To act as someone else, for example `acme/marcos.reis` (a manager), change it in `.env` and restart the server.

### Tests and lint
```bash
uv run --env-file .env pytest -rs   # without DATABASE_URL, the 89 database tests are skipped locally (CI fails instead)
uv run ruff check && uv run ruff format --check
```

## Roadmap
Login, database-enforced isolation, rate limits and the injection experiment are designed in [docs/design.md](docs/design.md) and [docs/threat-model.md](docs/threat-model.md). The hosting choices (Neon, continuous deployment), the local-model host, the chat panel and the demo are planned but not written up yet. These are the next pieces of work, in order.

1. **Login (OAuth 2.1).**
   - The server will become a resource server: Protected Resource Metadata, and a `401` that points clients to the identity provider.
   - Every token will be checked for signature, issuer, **audience** and expiry, and scopes will come from the token instead of the role table.
   - Tools outside your scopes will also be hidden from `tools/list`. Today they are listed, and already refused when called.
   - The development stand-ins for the caller (`DEV_USER`, the stand-in's header) will be replaced by verified tokens, one audience each: MCP or platform.
2. **Isolation enforced by the database.**
   - Row-level security on every tenant table, with `FORCE` and a non-owner application role, so a query that forgets its filter still sees nothing outside its tenant.
   - An insert-only grant on the audit log.
   - Tests that remove an app-level filter on purpose and show the database still blocks it.
3. **Deploy and operate.**
   - Docker on Google Cloud Run, Postgres on Neon, and secrets in Secret Manager.
   - A spend cap, a budget alert and an instance cap, set *before* the first deploy.
   - Serve legacy (≤ 2025-11-25) clients statelessly (`stateless_http=True`, design D8). This is decided but not wired yet. Today a legacy `initialize` still gets an in-memory session, which wouldn't survive a second instance.
   - Add the service's hostname to the SDK's allowed hosts, which accept only localhost today.
   - Continuous deployment with a post-deploy smoke test.
   - OpenTelemetry traces per tool call (tenant, role, outcome, result size, no content). Audit rows will carry the trace ID: the column exists, but nothing fills it yet.
   - Per-user and per-tenant rate limits, and alerts on refusal spikes.
4. **Evaluation and security testing.**
   - A reusable local-model MCP host (Ollama on a single GPU).
   - A task set scored on tool selection, arguments, task success and unauthorized-request handling, reporting server enforcement separately from model behavior.
   - An indirect prompt-injection experiment with before/after numbers.
     - The "after" adds labeling and delimiting of user-written text in results (design §6, principle 6).
     - Today only `list_announcements`' description warns the model, and `get_module` content carries no label.
5. **Polish.**
   - A chat panel in the platform stand-in, driven by the local model, once login exists.
   - A short demo video, and a written account of what real production would still add.

**Deliberately out of scope:**
- real users and their data;
- SSO/SCIM provisioning;
- embedding-based search (Postgres full-text search is enough here);
- MCP resources and prompts;
- the training platform itself (content authoring, recording completions, a real UI). Completions here come from the seed data.

## Repository layout
```
src/frontline_training_mcp/
  server.py          MCP server wiring and entry point
  tools.py           read tools: the model-facing contract (descriptions, schemas, hints)
  write_tools.py     write tools: the same, for writes
  service.py         every business rule, shared by all surfaces
  schemas.py         result shapes
  identity.py        who is calling: tenant, user, scopes, audience
  db.py              engine and the tenant-scoped transaction helper
  models.py          ORM mapping of the SQL schema
  migrate.py         the migration runner
  standin/           platform stand-in (development only; AI-generated UI)
migrations/          numbered plain-SQL migrations
seeds/               fake demo data
tests/               the tests, plus the reviewed tool-contract snapshot
docs/                design, threat model, wire captures
```

## How it was built
- Claude Code (an AI coding agent) wrote the code, in small increments.
- I set the scope, made the design decisions recorded in [docs/design.md](docs/design.md), and signed off the [threat model](docs/threat-model.md).
- I walked through the schema, the data layer and the read tools increment by increment, questioning the design choices before moving on.
- The platform stand-in also went through two AI-run adversarial review passes. Their confirmed findings and the fixes are listed in the messages of commits `032e813` and `603fded`.
