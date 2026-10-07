BEGIN;

ALTER TABLE pv.safety_cases
    ADD COLUMN IF NOT EXISTS regulatory_submitted_date DATE;

-- Backfill cases already marked Submitted or Closed after submission, using
-- the most recent audit entry that moved the case to Submitted.
UPDATE pv.safety_cases AS safety_cases
SET regulatory_submitted_date = submitted.submitted_on
FROM (
    SELECT
        case_id,
        MAX(performed_at)::date AS submitted_on
    FROM pv.case_audit_log
    WHERE details LIKE 'Status changed to Submitted.%'
    GROUP BY case_id
) AS submitted
WHERE submitted.case_id = safety_cases.case_id
  AND safety_cases.regulatory_submitted_date IS NULL;

COMMIT;
