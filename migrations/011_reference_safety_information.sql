BEGIN;

CREATE TABLE IF NOT EXISTS pv.reference_safety_information (
    rsi_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    product_name VARCHAR(255) NOT NULL,
    active_substance VARCHAR(255),
    reference_product_name VARCHAR(255),
    market VARCHAR(150),

    document_type VARCHAR(80) NOT NULL,
    document_version VARCHAR(100),
    effective_date DATE,

    source_url TEXT,
    original_filename VARCHAR(255),
    stored_filename VARCHAR(255) UNIQUE,
    content_type VARCHAR(150),
    file_size_bytes BIGINT,

    is_current BOOLEAN NOT NULL DEFAULT TRUE,

    uploaded_by BIGINT
        REFERENCES pv.users(user_id)
        ON DELETE SET NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CHECK (
        source_url IS NOT NULL
        OR original_filename IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_rsi_product
    ON pv.reference_safety_information (
        product_name,
        is_current
    );

CREATE TABLE IF NOT EXISTS pv.case_safety_assessments (
    assessment_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    case_id BIGINT NOT NULL UNIQUE
        REFERENCES pv.safety_cases(case_id)
        ON DELETE CASCADE,

    rsi_id BIGINT
        REFERENCES pv.reference_safety_information(rsi_id)
        ON DELETE SET NULL,

    event_term_assessed VARCHAR(500) NOT NULL,

    listedness_status VARCHAR(40) NOT NULL CHECK (
        listedness_status IN (
            'Listed',
            'Not listed',
            'Insufficient information'
        )
    ),

    expectedness_status VARCHAR(40) NOT NULL CHECK (
        expectedness_status IN (
            'Expected',
            'Unexpected',
            'Not assessable'
        )
    ),

    seriousness_assessment VARCHAR(40) NOT NULL CHECK (
        seriousness_assessment IN (
            'Serious',
            'Non-serious',
            'Not assessable'
        )
    ),

    seriousness_criteria TEXT,
    rsi_evidence TEXT,
    assessment_rationale TEXT,

    assessed_by BIGINT
        REFERENCES pv.users(user_id)
        ON DELETE SET NULL,

    assessed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_case_safety_assessments_rsi
    ON pv.case_safety_assessments (rsi_id);

COMMIT;