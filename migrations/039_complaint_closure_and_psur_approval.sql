BEGIN;

-- Complaints: migration 017 changed the allowed statuses but never removed
-- the original constraints, so legacy values ('New', 'Closed') remained.
ALTER TABLE pv.product_complaints
    DROP CONSTRAINT IF EXISTS product_complaint_status_check;
ALTER TABLE pv.product_complaints
    DROP CONSTRAINT IF EXISTS product_complaint_severity_check;
ALTER TABLE pv.product_complaints
    DROP CONSTRAINT IF EXISTS chk_product_complaints_status;
ALTER TABLE pv.product_complaints
    DROP CONSTRAINT IF EXISTS chk_product_complaints_severity;

UPDATE pv.product_complaints
SET status = CASE
        WHEN status = 'Closed' THEN 'Investigation complete'
        ELSE 'Under investigation'
    END
WHERE status NOT IN ('Under investigation', 'Investigation complete');

UPDATE pv.product_complaints
SET severity = 'Serious'
WHERE severity NOT IN ('Non-serious', 'Serious');

ALTER TABLE pv.product_complaints
    ALTER COLUMN status SET DEFAULT 'Under investigation';

ALTER TABLE pv.product_complaints
    ADD CONSTRAINT chk_product_complaints_status
    CHECK (status IN ('Under investigation', 'Investigation complete'));

ALTER TABLE pv.product_complaints
    ADD CONSTRAINT chk_product_complaints_severity
    CHECK (severity IN ('Non-serious', 'Serious'));

-- PSURs: record who approved and finalised the report, and when.
ALTER TABLE pv.psur_reports
    ADD COLUMN IF NOT EXISTS approved_by_user_id BIGINT
        REFERENCES pv.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS finalised_by_user_id BIGINT
        REFERENCES pv.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS finalised_at TIMESTAMPTZ;

COMMIT;
