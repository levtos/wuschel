ALTER TABLE cc_work_job DROP CONSTRAINT cc_work_job_phase_check;

ALTER TABLE cc_work_job ADD CONSTRAINT cc_work_job_phase_check CHECK (
    phase IN ('receiving', 'waiting', 'running', 'prepared', 'committing',
              'finished', 'failed', 'cancelled')
);

ALTER TABLE cc_work_job ADD COLUMN prepared_operation text;

CREATE INDEX cc_work_job_prepared
    ON cc_work_job (created_at, id) WHERE phase = 'prepared';
