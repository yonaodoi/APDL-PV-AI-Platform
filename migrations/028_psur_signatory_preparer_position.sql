BEGIN;

ALTER TABLE pv.psur_reports
    ADD COLUMN IF NOT EXISTS prepared_by_position VARCHAR(200);

COMMIT;