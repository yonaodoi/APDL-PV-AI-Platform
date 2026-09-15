BEGIN;

ALTER TABLE pv.psur_reports
    ADD COLUMN marketing_authorisation_number TEXT,
    ADD COLUMN marketing_authorisation_date DATE;

COMMIT;