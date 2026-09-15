BEGIN;

CREATE TABLE pv.safety_cases (
    case_id BIGSERIAL PRIMARY KEY,
    case_number VARCHAR(30) NOT NULL UNIQUE,
    case_type VARCHAR(30) NOT NULL DEFAULT 'ADR',
    workflow_status VARCHAR(30) NOT NULL DEFAULT 'New',
    received_date DATE NOT NULL,
    country_id INTEGER REFERENCES pv.countries(country_id),
    source VARCHAR(100) NOT NULL,
    report_type VARCHAR(50),
    reporter_name VARCHAR(200),
    reporter_profession VARCHAR(100),
    reporter_organisation VARCHAR(200),
    reporter_phone VARCHAR(100),
    reporter_email VARCHAR(255),
    patient_initials VARCHAR(20),
    patient_age_years INTEGER,
    patient_age_unit VARCHAR(20) DEFAULT 'Years',
    patient_sex VARCHAR(20),
    patient_weight_kg NUMERIC(6, 2),
    medical_history TEXT,
    concomitant_medicines TEXT,
    event_description TEXT NOT NULL,
    event_onset_date DATE,
    event_outcome VARCHAR(100),
    seriousness BOOLEAN NOT NULL DEFAULT FALSE,
    seriousness_criteria TEXT,
    causality_assessment VARCHAR(100),
    case_narrative TEXT,
    follow_up_required BOOLEAN NOT NULL DEFAULT FALSE,
    follow_up_due_date DATE,
    created_by INTEGER NOT NULL REFERENCES pv.users(user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE pv.case_products (
    case_product_id BIGSERIAL PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES pv.safety_cases(case_id) ON DELETE CASCADE,
    product_role VARCHAR(30) NOT NULL DEFAULT 'Suspect',
    product_name VARCHAR(255) NOT NULL,
    generic_name VARCHAR(255),
    strength VARCHAR(100),
    dosage_form VARCHAR(100),
    batch_number VARCHAR(100),
    expiry_date DATE,
    dose VARCHAR(100),
    route VARCHAR(100),
    indication TEXT,
    therapy_start_date DATE,
    therapy_end_date DATE,
    action_taken VARCHAR(150),
    dechallenge_result VARCHAR(150),
    rechallenge_result VARCHAR(150)
);

CREATE TABLE pv.case_attachments (
    attachment_id BIGSERIAL PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES pv.safety_cases(case_id) ON DELETE CASCADE,
    original_filename VARCHAR(500) NOT NULL,
    stored_filename VARCHAR(500) NOT NULL,
    content_type VARCHAR(150),
    file_size_bytes BIGINT,
    uploaded_by INTEGER NOT NULL REFERENCES pv.users(user_id),
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE pv.case_audit_log (
    audit_id BIGSERIAL PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES pv.safety_cases(case_id) ON DELETE CASCADE,
    action VARCHAR(100) NOT NULL,
    details TEXT,
    performed_by INTEGER NOT NULL REFERENCES pv.users(user_id),
    performed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_safety_cases_status
    ON pv.safety_cases(workflow_status);

CREATE INDEX idx_safety_cases_received_date
    ON pv.safety_cases(received_date);

CREATE INDEX idx_safety_cases_country
    ON pv.safety_cases(country_id);

CREATE INDEX idx_case_products_case
    ON pv.case_products(case_id);

COMMIT;