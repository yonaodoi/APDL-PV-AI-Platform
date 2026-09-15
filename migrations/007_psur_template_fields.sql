BEGIN;

ALTER TABLE pv.psur_reports
    ADD COLUMN active_substances TEXT,
    ADD COLUMN atc_codes TEXT,
    ADD COLUMN marketing_authorisation_procedure TEXT,
    ADD COLUMN international_birth_date DATE,
    ADD COLUMN eurd DATE,
    ADD COLUMN marketing_authorisation_holder_name TEXT,
    ADD COLUMN marketing_authorisation_holder_address TEXT,
    ADD COLUMN qppv_name VARCHAR(200),
    ADD COLUMN qppv_phone VARCHAR(100),
    ADD COLUMN qppv_email VARCHAR(200),
    ADD COLUMN therapeutic_indication TEXT,
    ADD COLUMN mechanism_of_action TEXT,
    ADD COLUMN serial_number VARCHAR(100);

COMMIT;