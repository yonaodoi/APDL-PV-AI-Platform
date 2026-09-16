BEGIN;

CREATE TABLE IF NOT EXISTS pv.case_ai_assessment_reports (
    ai_report_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    case_id BIGINT NOT NULL
        REFERENCES pv.safety_cases(case_id)
        ON DELETE CASCADE,

    report_text TEXT NOT NULL,

    model_name VARCHAR(100) NOT NULL DEFAULT 'llama3.2:3b',

    generation_status VARCHAR(30) NOT NULL DEFAULT 'Generated'
        CHECK (
            generation_status IN (
                'Generated',
                'Approved',
                'Superseded'
            )
        ),

    generated_by BIGINT
        REFERENCES pv.users(user_id)
        ON DELETE SET NULL,

    approved_by BIGINT
        REFERENCES pv.users(user_id)
        ON DELETE SET NULL,

    approved_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_case_ai_reports_case
    ON pv.case_ai_assessment_reports (case_id, created_at DESC);

COMMIT;