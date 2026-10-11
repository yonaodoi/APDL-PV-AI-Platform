-- Company product catalogue, and Licences & Integrations administration.
BEGIN;

-- Product catalogue ---------------------------------------------------------
CREATE TABLE IF NOT EXISTS pv.product_catalogue (
    product_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_code VARCHAR(100) NOT NULL UNIQUE,
    brand_name VARCHAR(255) NOT NULL,
    generic_name VARCHAR(500),
    strength VARCHAR(150),
    dosage_form VARCHAR(150),
    routes VARCHAR(255),
    manufacturer VARCHAR(255),
    mah VARCHAR(255),
    registration_number VARCHAR(150),
    market VARCHAR(150),
    status VARCHAR(30) NOT NULL DEFAULT 'Active',
    rsi_id BIGINT REFERENCES pv.reference_safety_information(rsi_id) ON DELETE SET NULL,
    notes TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT product_catalogue_status_check
        CHECK (status IN ('Active', 'Pending approval', 'Suspended', 'Withdrawn', 'Discontinued'))
);
CREATE INDEX IF NOT EXISTS idx_product_catalogue_brand ON pv.product_catalogue (LOWER(brand_name));
CREATE INDEX IF NOT EXISTS idx_product_catalogue_generic ON pv.product_catalogue (LOWER(generic_name));

-- Every saved version, so records can show exactly what the catalogue said.
CREATE TABLE IF NOT EXISTS pv.product_catalogue_versions (
    version_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES pv.product_catalogue(product_id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    snapshot JSONB NOT NULL,
    change_note TEXT,
    changed_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (product_id, version)
);

CREATE TABLE IF NOT EXISTS pv.product_import_batches (
    batch_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    original_filename VARCHAR(255) NOT NULL,
    file_sha256 VARCHAR(64) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'Preview',
    rows JSONB NOT NULL,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    uploaded_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    applied_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    applied_at TIMESTAMPTZ,
    CONSTRAINT product_import_status_check CHECK (status IN ('Preview', 'Applied', 'Discarded'))
);

-- Records keep the catalogue reference and a snapshot of what was used.
ALTER TABLE pv.case_products
    ADD COLUMN IF NOT EXISTS catalogue_product_id BIGINT REFERENCES pv.product_catalogue(product_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS catalogue_version INTEGER,
    ADD COLUMN IF NOT EXISTS catalogue_snapshot JSONB;
ALTER TABLE pv.product_complaints
    ADD COLUMN IF NOT EXISTS catalogue_product_id BIGINT REFERENCES pv.product_catalogue(product_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS catalogue_version INTEGER,
    ADD COLUMN IF NOT EXISTS catalogue_snapshot JSONB;
ALTER TABLE pv.safety_signals
    ADD COLUMN IF NOT EXISTS catalogue_product_id BIGINT REFERENCES pv.product_catalogue(product_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS catalogue_version INTEGER,
    ADD COLUMN IF NOT EXISTS catalogue_snapshot JSONB;

-- Licences and integrations ------------------------------------------------
CREATE TABLE IF NOT EXISTS pv.integrations (
    integration_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind VARCHAR(30) NOT NULL,
    name VARCHAR(150) NOT NULL,
    provider VARCHAR(150),
    licence_reference VARCHAR(150),
    registration_date DATE,
    expiry_date DATE,
    organisation VARCHAR(255),
    permitted_use TEXT,
    dictionary_version VARCHAR(50),
    api_endpoint VARCHAR(500),
    model VARCHAR(150),
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    credential_encrypted TEXT,
    credential_hint VARCHAR(20),
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(30) NOT NULL DEFAULT 'Not tested',
    last_test_at TIMESTAMPTZ,
    last_test_result TEXT,
    last_sync_at TIMESTAMPTZ,
    last_sync_note TEXT,
    created_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT integrations_kind_check CHECK (kind IN ('MedDRA', 'WHODrug', 'AI provider', 'Other API'))
);

-- AI usage, for spending limits and troubleshooting (no prompts or results).
CREATE TABLE IF NOT EXISTS pv.ai_usage (
    usage_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    purpose VARCHAR(40),
    provider VARCHAR(40),
    model VARCHAR(150),
    input_tokens INTEGER,
    output_tokens INTEGER,
    estimated_cost NUMERIC(12, 6),
    duration_ms INTEGER,
    status VARCHAR(20) NOT NULL,
    error_message TEXT,
    attempt INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_time ON pv.ai_usage (occurred_at DESC);

COMMIT;
