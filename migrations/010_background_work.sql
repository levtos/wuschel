CREATE TABLE cc_work_job (
    id uuid PRIMARY KEY,
    owner text NOT NULL,
    operation text NOT NULL,
    request_hash text NOT NULL,
    metadata jsonb NOT NULL,
    phase text NOT NULL CHECK (
        phase IN ('receiving', 'waiting', 'running', 'finished', 'failed', 'cancelled')
    ),
    result_status integer,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    error_code text
);

CREATE INDEX cc_work_job_waiting ON cc_work_job (created_at, id) WHERE phase = 'waiting';
CREATE INDEX cc_work_job_owner ON cc_work_job (owner, created_at DESC, id);

CREATE TABLE cc_work_artifact (
    job_id uuid NOT NULL REFERENCES cc_work_job (id) ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('request', 'result', 'plan')),
    part integer NOT NULL CHECK (part >= 0),
    content bytea NOT NULL,
    PRIMARY KEY (job_id, kind, part)
);

CREATE TABLE cc_work_plan (
    kind text NOT NULL CHECK (kind IN ('catalog_cleanup', 'title_sync')),
    plan_key text NOT NULL,
    owner text NOT NULL,
    job_id uuid NOT NULL REFERENCES cc_work_job (id) ON DELETE CASCADE,
    PRIMARY KEY (kind, plan_key, owner)
);

CREATE INDEX cc_title_sync_plan_progress
    ON catalog_request ((payload->'title_sync_progress'->>'plan_id'),
                        (payload->'title_sync_progress'->>'owner'),
                        (((payload->'title_sync_progress'->>'applied_page')::integer)) DESC)
    WHERE payload ? 'title_sync_progress';
