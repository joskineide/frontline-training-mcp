-- 001: the domain model (docs/design.md §3).
--
-- Every tenant-owned table carries tenant_id, and its keys start with it.
-- Composite foreign keys make cross-tenant references impossible in the database itself:
-- an assignment can only point at a worker and a module of its *own* tenant.
-- Row-level security policies come in a later migration.

CREATE TABLE tenant (
    tenant_id text PRIMARY KEY CHECK (tenant_id ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
    name      text NOT NULL
);

CREATE TABLE team (
    tenant_id  text NOT NULL REFERENCES tenant,
    team_id    text NOT NULL,
    name       text NOT NULL,
    manager_id text NOT NULL,
    PRIMARY KEY (tenant_id, team_id)
);

CREATE TABLE app_user (
    tenant_id        text NOT NULL REFERENCES tenant,
    user_id          text NOT NULL CHECK (user_id ~ '^[a-z0-9]+([.-][a-z0-9]+)*$'),
    team_id          text,  -- the team a worker belongs to; a manager leads one via team.manager_id
    display_name     text NOT NULL,
    role             text NOT NULL CHECK (role IN ('worker', 'manager')),
    external_subject text UNIQUE,  -- the identity provider's `sub`, linked once login exists
    PRIMARY KEY (tenant_id, user_id),
    FOREIGN KEY (tenant_id, team_id) REFERENCES team (tenant_id, team_id)
);

-- team and app_user reference each other. Checking this side at commit time
-- lets both rows be inserted in the same transaction.
ALTER TABLE team
    ADD FOREIGN KEY (tenant_id, manager_id) REFERENCES app_user (tenant_id, user_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE module (
    tenant_id        text NOT NULL REFERENCES tenant,
    module_id        text NOT NULL CHECK (module_id ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),  -- a slug (D1)
    title            text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    summary          text NOT NULL,
    content          text NOT NULL,  -- written by the tenant's staff: untrusted text
    duration_minutes integer NOT NULL CHECK (duration_minutes > 0),
    search_vector    tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', title), 'A')
        || setweight(to_tsvector('english', summary), 'B')
        || setweight(to_tsvector('english', content), 'C')
    ) STORED,
    PRIMARY KEY (tenant_id, module_id)
);
CREATE INDEX module_search_idx ON module USING gin (search_vector);

CREATE TABLE assignment (
    tenant_id     text NOT NULL,
    assignment_id bigint GENERATED ALWAYS AS IDENTITY,
    worker_id     text NOT NULL,
    module_id     text NOT NULL,
    assigned_by   text NOT NULL,
    due_date      date,
    assigned_at   timestamptz NOT NULL DEFAULT now(),
    completed_at  timestamptz,  -- status is derived: completed if set, otherwise open (D10)
    PRIMARY KEY (tenant_id, assignment_id),
    FOREIGN KEY (tenant_id, worker_id)   REFERENCES app_user (tenant_id, user_id),
    FOREIGN KEY (tenant_id, module_id)   REFERENCES module (tenant_id, module_id),
    FOREIGN KEY (tenant_id, assigned_by) REFERENCES app_user (tenant_id, user_id)
);
-- At most one *open* assignment per worker and module: repeated calls stay idempotent,
-- and a completed module can be assigned again for recertification (D13).
CREATE UNIQUE INDEX assignment_one_open_idx
    ON assignment (tenant_id, worker_id, module_id) WHERE completed_at IS NULL;
CREATE INDEX assignment_worker_idx ON assignment (tenant_id, worker_id);

CREATE TABLE announcement (
    tenant_id       text NOT NULL,
    announcement_id bigint GENERATED ALWAYS AS IDENTITY,
    team_id         text NOT NULL,
    author_id       text NOT NULL,
    title           text NOT NULL CHECK (length(title) BETWEEN 1 AND 120),
    body            text NOT NULL CHECK (length(body) BETWEEN 1 AND 2000),  -- untrusted text
    status          text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'published')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    published_at    timestamptz,
    published_by    text,  -- always a human, outside MCP (D12)
    PRIMARY KEY (tenant_id, announcement_id),
    FOREIGN KEY (tenant_id, team_id)      REFERENCES team (tenant_id, team_id),
    FOREIGN KEY (tenant_id, author_id)    REFERENCES app_user (tenant_id, user_id),
    FOREIGN KEY (tenant_id, published_by) REFERENCES app_user (tenant_id, user_id),
    CHECK ((status = 'published') = (published_at IS NOT NULL AND published_by IS NOT NULL))
);
CREATE INDEX announcement_feed_idx
    ON announcement (tenant_id, team_id, published_at DESC) WHERE status = 'published';

-- Append-only record of every write. No foreign key on actor_id: history must outlive the user.
CREATE TABLE audit_log (
    audit_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES tenant,
    actor_id  text NOT NULL,
    tool      text NOT NULL,
    target    jsonb NOT NULL DEFAULT '{}',  -- IDs only, never free text
    outcome   text NOT NULL,
    at        timestamptz NOT NULL DEFAULT now(),
    trace_id  text
);
CREATE INDEX audit_log_tenant_at_idx ON audit_log (tenant_id, at);
