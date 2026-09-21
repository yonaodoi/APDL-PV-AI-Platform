BEGIN;

ALTER TABLE pv.psur_reports
    ADD COLUMN IF NOT EXISTS pbrer_contact_name VARCHAR(200);

ALTER TABLE pv.psur_reports
    ADD COLUMN IF NOT EXISTS pbrer_contact_position VARCHAR(200);

ALTER TABLE pv.psur_reports
    ADD COLUMN IF NOT EXISTS reviewer_a_name VARCHAR(200);

ALTER TABLE pv.psur_reports
    ADD COLUMN IF NOT EXISTS reviewer_a_position VARCHAR(200);

COMMIT;