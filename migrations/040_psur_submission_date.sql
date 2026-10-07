BEGIN;

-- Date the finalised PSUR was submitted to the regulator(s).
ALTER TABLE pv.psur_reports
    ADD COLUMN IF NOT EXISTS submitted_date DATE;

COMMIT;
