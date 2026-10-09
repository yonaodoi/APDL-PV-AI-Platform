-- Uploaded Word templates for reports. One active template per report type;
-- each upload is checked by test-filling it before it can be made active.

BEGIN;

CREATE TABLE IF NOT EXISTS pv.report_templates (
    template_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    report_type VARCHAR(40) NOT NULL,
    name VARCHAR(200) NOT NULL,
    version VARCHAR(40),
    original_filename VARCHAR(255) NOT NULL,
    stored_filename VARCHAR(255) NOT NULL UNIQUE,
    source_filename VARCHAR(255),
    source VARCHAR(20) NOT NULL DEFAULT 'markers'
        CHECK (source IN ('markers', 'ai_mapped')),
    status VARCHAR(20) NOT NULL DEFAULT 'Draft'
        CHECK (status IN ('Draft', 'Active', 'Historic')),
    check_passed BOOLEAN NOT NULL DEFAULT FALSE,
    check_problems JSONB NOT NULL DEFAULT '[]'::jsonb,
    check_notes JSONB NOT NULL DEFAULT '[]'::jsonb,
    markers JSONB NOT NULL DEFAULT '[]'::jsonb,
    mapping JSONB,
    mapping_applied BOOLEAN NOT NULL DEFAULT TRUE,
    uploaded_by BIGINT REFERENCES pv.users(user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_by BIGINT REFERENCES pv.users(user_id),
    activated_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_report_templates_one_active
    ON pv.report_templates(report_type)
    WHERE status = 'Active';

CREATE INDEX IF NOT EXISTS idx_report_templates_type
    ON pv.report_templates(report_type, created_at DESC);

COMMIT;
