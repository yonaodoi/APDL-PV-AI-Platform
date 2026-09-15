BEGIN;

CREATE TABLE pv.safety_signals (
    signal_id BIGSERIAL PRIMARY KEY,
    signal_number VARCHAR(100) NOT NULL UNIQUE,
    date_detected DATE NOT NULL,

    product_name VARCHAR(200) NOT NULL,
    event_term VARCHAR(300) NOT NULL,
    signal_source VARCHAR(100) NOT NULL,
    signal_description TEXT NOT NULL,

    priority VARCHAR(30) NOT NULL DEFAULT 'Medium',
    status VARCHAR(50) NOT NULL DEFAULT 'New',

    assessment_summary TEXT,
    decision_summary TEXT,
    owner_name VARCHAR(200),

    created_by BIGINT REFERENCES pv.users(user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT safety_signal_priority_check
        CHECK (priority IN ('Low', 'Medium', 'High', 'Critical')),

    CONSTRAINT safety_signal_status_check
        CHECK (status IN (
            'New',
            'Under evaluation',
            'Validated',
            'Closed'
        ))
);

CREATE INDEX safety_signals_status_idx
    ON pv.safety_signals(status);

CREATE INDEX safety_signals_detected_date_idx
    ON pv.safety_signals(date_detected DESC);

COMMIT;