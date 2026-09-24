BEGIN;

CREATE TABLE IF NOT EXISTS pv.case_completeness_checks (
    check_id BIGSERIAL PRIMARY KEY,
    case_id BIGINT NOT NULL
        REFERENCES pv.safety_cases(case_id) ON DELETE CASCADE,
    check_code VARCHAR(80) NOT NULL,
    check_label VARCHAR(150) NOT NULL,
    status VARCHAR(20) NOT NULL
        CHECK (status IN ('Pass', 'Review')),
    message TEXT NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (case_id, check_code)
);

CREATE INDEX IF NOT EXISTS idx_case_completeness_status
    ON pv.case_completeness_checks(status);

COMMIT;
