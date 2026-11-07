CREATE TABLE IF NOT EXISTS users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    login text UNIQUE NOT NULL,
    password_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS code_versions (
    sha text PRIMARY KEY,
    kind text NOT NULL,
    name text NOT NULL,
    code text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS procedures (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid REFERENCES users(id),
    kind text NOT NULL CHECK (kind IN ('normalize', 'compare', 'segments', 'facts', 'check')),
    name text NOT NULL,
    origin text NOT NULL CHECK (origin IN ('workspace', 'file')),
    path text,
    sha text NOT NULL REFERENCES code_versions(sha),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS run_configs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES users(id),
    name text NOT NULL,
    dataset text,
    procedure_ids jsonb NOT NULL,
    params jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id uuid NOT NULL REFERENCES users(id),
    config_id uuid REFERENCES run_configs(id),
    name text NOT NULL,
    state text NOT NULL DEFAULT 'receiving'
        CHECK (state IN ('receiving', 'sealed', 'finalizing', 'done', 'failed', 'cancelled')),
    procedures jsonb NOT NULL,
    params jsonb NOT NULL DEFAULT '{}',
    total_chunks int,
    done_chunks int NOT NULL DEFAULT 0,
    failed_chunks int NOT NULL DEFAULT 0,
    apps int NOT NULL DEFAULT 0,
    stats jsonb NOT NULL DEFAULT '{}',
    report_params jsonb,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    sealed_at timestamptz,
    finished_at timestamptz
);
CREATE INDEX IF NOT EXISTS runs_owner ON runs (owner_id, created_at DESC);

CREATE TABLE IF NOT EXISTS chunks (
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    chunk_no int NOT NULL,
    state text NOT NULL DEFAULT 'ready'
        CHECK (state IN ('ready', 'queued', 'running', 'done', 'failed', 'cancelled')),
    input_uri text NOT NULL,
    apps int NOT NULL,
    attempts int NOT NULL DEFAULT 0,
    queued_at timestamptz,
    started_at timestamptz,
    finished_at timestamptz,
    worker text,
    output_uri text,
    stats jsonb,
    error text,
    PRIMARY KEY (run_id, chunk_no)
);
CREATE INDEX IF NOT EXISTS chunks_pending ON chunks (run_id, state) WHERE state IN ('ready', 'queued', 'running');

CREATE TABLE IF NOT EXISTS stage_runs (
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    chunk_no int NOT NULL,
    stage text NOT NULL,
    code_sha text,
    status text NOT NULL,
    started_at timestamptz NOT NULL,
    duration_ms real NOT NULL,
    rows_in int,
    rows_out int,
    error text,
    PRIMARY KEY (run_id, chunk_no, stage)
);

CREATE TABLE IF NOT EXISTS run_events (
    id bigserial PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ts timestamptz NOT NULL DEFAULT now(),
    type text NOT NULL,
    data jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS run_events_run ON run_events (run_id, id);

CREATE TABLE IF NOT EXISTS check_results (
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    check_id text NOT NULL,
    check_sha text NOT NULL,
    label text NOT NULL,
    rows jsonb NOT NULL,
    duration_ms real NOT NULL,
    error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, check_id, check_sha)
);

CREATE TABLE IF NOT EXISTS batches (
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    source text NOT NULL,
    first_chunk_no int NOT NULL,
    chunks int,
    done boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, source)
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash bytea PRIMARY KEY,
    user_id uuid REFERENCES users(id) ON DELETE CASCADE,
    run_id uuid REFERENCES runs(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS shares (
    token_hash bytea PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    created_by uuid NOT NULL REFERENCES users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS cell_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    run_id uuid REFERENCES runs(id) ON DELETE CASCADE,
    code_sha text NOT NULL REFERENCES code_versions(sha),
    state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'running', 'done', 'failed')),
    result jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);

CREATE TABLE IF NOT EXISTS audit (
    id bigserial PRIMARY KEY,
    ts timestamptz NOT NULL DEFAULT now(),
    user_id uuid,
    action text NOT NULL,
    target text,
    data jsonb NOT NULL DEFAULT '{}'
);

ALTER TABLE runs ADD COLUMN IF NOT EXISTS report_params jsonb;
