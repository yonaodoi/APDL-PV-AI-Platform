BEGIN;

CREATE TABLE IF NOT EXISTS pv.case_ai_assessment_generation_jobs (
    job_id BIGSERIAL PRIMARY KEY,

    case_id BIGINT NOT NULL
        REFERENCES pv.safety_cases(case_id)
        ON DELETE CASCADE,

    requested_by BIGINT
        REFERENCES pv.users(user_id)
        ON DELETE SET NULL,

    status VARCHAR(20) NOT NULL DEFAULT 'Queued'
        CHECK (status IN ('Queued', 'Processing', 'Completed', 'Failed')),

    current_stage VARCHAR(300) NOT NULL DEFAULT
        'Assessment request received.',

    error_message TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS case_ai_assessment_jobs_case_idx
    ON pv.case_ai_assessment_generation_jobs (
        case_id,
        created_at DESC
    );

CREATE INDEX IF NOT EXISTS case_ai_assessment_jobs_status_idx
    ON pv.case_ai_assessment_generation_jobs (status);

COMMIT;