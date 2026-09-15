BEGIN;

ALTER TABLE pv.safety_cases
    ADD COLUMN patient_pregnancy_status VARCHAR(30),
    ADD COLUMN patient_address TEXT,
    ADD COLUMN patient_phone VARCHAR(100),
    ADD COLUMN patient_date_of_birth DATE,
    ADD COLUMN event_onset_time TIME,
    ADD COLUMN event_end_date DATE,
    ADD COLUMN treatment_given TEXT,
    ADD COLUMN laboratory_results TEXT,
    ADD COLUMN report_title VARCHAR(255),
    ADD COLUMN form_id VARCHAR(100);

ALTER TABLE pv.case_products
    ADD COLUMN frequency VARCHAR(100);

COMMIT;