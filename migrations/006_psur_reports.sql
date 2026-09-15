BEGIN;

CREATE TABLE pv.psur_reports (
    psur_id BIGSERIAL PRIMARY KEY,
    report_number VARCHAR(100) NOT NULL UNIQUE,
    product_name VARCHAR(200) NOT NULL,

    reporting_period_start DATE NOT NULL,
    reporting_period_end DATE NOT NULL,
    data_lock_point DATE NOT NULL,

    countries_covered TEXT,
    status VARCHAR(50) NOT NULL DEFAULT 'Draft',
    prepared_by VARCHAR(200),
    approved_by VARCHAR(200),
    report_notes TEXT,

    created_by BIGINT REFERENCES pv.users(user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT psur_reporting_period_check
        CHECK (reporting_period_end >= reporting_period_start),

    CONSTRAINT psur_status_check
        CHECK (status IN (
            'Draft',
            'Under review',
            'Approved',
            'Finalised'
        ))
);

CREATE INDEX psur_reports_period_idx
    ON pv.psur_reports(reporting_period_end DESC);

CREATE INDEX psur_reports_status_idx
    ON pv.psur_reports(status);

COMMIT;