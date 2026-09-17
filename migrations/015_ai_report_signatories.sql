BEGIN;

ALTER TABLE pv.case_ai_assessment_reports
    ADD COLUMN IF NOT EXISTS prepared_by_name VARCHAR(200);

ALTER TABLE pv.case_ai_assessment_reports
    ADD COLUMN IF NOT EXISTS prepared_by_designation VARCHAR(250);

ALTER TABLE pv.case_ai_assessment_reports
    ADD COLUMN IF NOT EXISTS reviewed_by_name VARCHAR(200);

ALTER TABLE pv.case_ai_assessment_reports
    ADD COLUMN IF NOT EXISTS reviewed_by_designation VARCHAR(250);

ALTER TABLE pv.case_ai_assessment_reports
    ADD COLUMN IF NOT EXISTS authorised_by_name VARCHAR(200);

ALTER TABLE pv.case_ai_assessment_reports
    ADD COLUMN IF NOT EXISTS authorised_by_designation VARCHAR(250);

COMMIT;