BEGIN;

CREATE TABLE pv.product_complaints (
    complaint_id BIGSERIAL PRIMARY KEY,
    complaint_number VARCHAR(100) NOT NULL UNIQUE,
    date_received DATE NOT NULL,
    country_id BIGINT REFERENCES pv.countries(country_id),
    reporter_name VARCHAR(200),
    reporter_contact VARCHAR(200),

    product_name VARCHAR(200) NOT NULL,
    batch_number VARCHAR(100),
    manufacturing_date DATE,
    expiry_date DATE,

    complaint_category VARCHAR(100) NOT NULL,
    complaint_description TEXT NOT NULL,
    severity VARCHAR(30) NOT NULL DEFAULT 'Non-serious',
    status VARCHAR(50) NOT NULL DEFAULT 'New',

    investigation_summary TEXT,
    corrective_action TEXT,
    closure_date DATE,

    created_by BIGINT REFERENCES pv.users(user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT product_complaint_severity_check
        CHECK (severity IN ('Non-serious', 'Serious', 'Critical')),

    CONSTRAINT product_complaint_status_check
        CHECK (status IN (
            'New',
            'Under investigation',
            'Awaiting information',
            'Closed'
        ))
);

CREATE INDEX product_complaints_status_idx
    ON pv.product_complaints(status);

CREATE INDEX product_complaints_received_date_idx
    ON pv.product_complaints(date_received DESC);

COMMIT;