-- E2B(R2)/E2B(R3) exports: every generated file is kept as a numbered
-- version with its check results. Nothing here transmits anything.
BEGIN;

CREATE SEQUENCE IF NOT EXISTS pv.e2b_message_seq START 1;

CREATE TABLE IF NOT EXISTS pv.e2b_exports (
    export_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES pv.safety_cases(case_id) ON DELETE CASCADE,
    export_version INTEGER NOT NULL,
    e2b_version VARCHAR(5) NOT NULL CHECK (e2b_version IN ('R2', 'R3')),
    recipient_key VARCHAR(50) NOT NULL,
    recipient_name VARCHAR(200) NOT NULL,
    receiver_identifier VARCHAR(100),
    message_number VARCHAR(50) NOT NULL,
    safety_report_id VARCHAR(200) NOT NULL,
    -- Draft: has errors, not for submission. Checked: passed the built-in
    -- checks. Validated: also passed the official ICH schema installed locally.
    status VARCHAR(20) NOT NULL CHECK (status IN ('Draft', 'Checked', 'Validated')),
    issues JSONB NOT NULL DEFAULT '[]'::jsonb,
    schema_used VARCHAR(200),
    meddra_version VARCHAR(50),
    whodrug_version VARCHAR(100),
    xml_text TEXT NOT NULL,
    file_sha256 VARCHAR(64) NOT NULL,
    case_updated_at TIMESTAMPTZ,
    created_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    downloaded_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE (case_id, export_version)
);
CREATE INDEX IF NOT EXISTS idx_e2b_exports_case ON pv.e2b_exports (case_id, export_version DESC);

COMMIT;
