-- MedDRA / WHODrug coding and the separate local terminology.
-- Official dictionary content is only ever loaded from licensed release files
-- (or a configured API); nothing here invents official codes.
BEGIN;

-- Imported dictionary releases ---------------------------------------------
CREATE TABLE IF NOT EXISTS pv.dictionary_imports (
    import_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dictionary VARCHAR(20) NOT NULL CHECK (dictionary IN ('MedDRA', 'WHODrug')),
    version VARCHAR(50) NOT NULL,
    language VARCHAR(20) NOT NULL DEFAULT 'English',
    source_filename VARCHAR(255),
    file_sha256 VARCHAR(64),
    term_count INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'Active' CHECK (status IN ('Active', 'Superseded', 'Failed')),
    notes TEXT,
    imported_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- MedDRA (from the MSSO ASCII release: llt.asc, pt.asc, soc.asc, mdhier.asc)
CREATE TABLE IF NOT EXISTS pv.meddra_llt (
    version VARCHAR(20) NOT NULL,
    llt_code VARCHAR(10) NOT NULL,
    llt_name VARCHAR(255) NOT NULL,
    pt_code VARCHAR(10) NOT NULL,
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (version, llt_code)
);
CREATE INDEX IF NOT EXISTS idx_meddra_llt_name ON pv.meddra_llt (version, LOWER(llt_name));

CREATE TABLE IF NOT EXISTS pv.meddra_pt (
    version VARCHAR(20) NOT NULL,
    pt_code VARCHAR(10) NOT NULL,
    pt_name VARCHAR(255) NOT NULL,
    primary_soc_code VARCHAR(10),
    PRIMARY KEY (version, pt_code)
);

CREATE TABLE IF NOT EXISTS pv.meddra_soc (
    version VARCHAR(20) NOT NULL,
    soc_code VARCHAR(10) NOT NULL,
    soc_name VARCHAR(255) NOT NULL,
    soc_abbrev VARCHAR(10),
    PRIMARY KEY (version, soc_code)
);

CREATE TABLE IF NOT EXISTS pv.meddra_hierarchy (
    version VARCHAR(20) NOT NULL,
    pt_code VARCHAR(10) NOT NULL,
    hlt_code VARCHAR(10),
    hlt_name VARCHAR(255),
    hlgt_code VARCHAR(10),
    hlgt_name VARCHAR(255),
    soc_code VARCHAR(10),
    soc_name VARCHAR(255),
    is_primary BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_meddra_hierarchy_pt ON pv.meddra_hierarchy (version, pt_code);

-- WHODrug (from a licensed WHODrug extract with named columns)
CREATE TABLE IF NOT EXISTS pv.whodrug_products (
    version VARCHAR(50) NOT NULL,
    drug_code VARCHAR(30) NOT NULL,
    drug_name VARCHAR(500) NOT NULL,
    drug_record_number VARCHAR(20),
    sequence_1 VARCHAR(5),
    sequence_2 VARCHAR(5),
    is_preferred BOOLEAN NOT NULL DEFAULT FALSE,
    ingredients TEXT,
    atc_codes VARCHAR(255),
    country VARCHAR(100),
    marketing_holder VARCHAR(255),
    PRIMARY KEY (version, drug_code)
);
CREATE INDEX IF NOT EXISTS idx_whodrug_name ON pv.whodrug_products (version, LOWER(drug_name));

-- Local terminology (clearly separate from the official dictionaries) -----
-- Events: the existing local event dictionary becomes the local event
-- terminology, with labelled local identifiers and approval status.
ALTER TABLE pv.event_terms
    ADD COLUMN IF NOT EXISTS local_code VARCHAR(20),
    ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'Approved',
    ADD COLUMN IF NOT EXISTS source_wording TEXT,
    ADD COLUMN IF NOT EXISTS description TEXT,
    ADD COLUMN IF NOT EXISTS created_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS approved_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS mapped_dictionary VARCHAR(20),
    ADD COLUMN IF NOT EXISTS mapped_code VARCHAR(30),
    ADD COLUMN IF NOT EXISTS mapped_term VARCHAR(255),
    ADD COLUMN IF NOT EXISTS mapped_version VARCHAR(50);
UPDATE pv.event_terms SET local_code = 'LOCAL-E-' || LPAD(term_id::text, 5, '0') WHERE local_code IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_event_terms_local_code ON pv.event_terms (local_code);
ALTER TABLE pv.event_terms ALTER COLUMN system_organ_class DROP NOT NULL;

CREATE TABLE IF NOT EXISTS pv.local_product_terms (
    local_product_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    local_code VARCHAR(20) UNIQUE,
    name VARCHAR(500) NOT NULL,
    ingredients TEXT,
    source_wording TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'Proposed' CHECK (status IN ('Proposed', 'Approved', 'Rejected')),
    mapped_dictionary VARCHAR(20),
    mapped_code VARCHAR(30),
    mapped_term VARCHAR(500),
    mapped_version VARCHAR(50),
    created_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    approved_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_local_product_name ON pv.local_product_terms (LOWER(name));

-- Wording a reviewer confirmed for a term ("remember this wording").
CREATE TABLE IF NOT EXISTS pv.coding_synonyms (
    synonym_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dictionary VARCHAR(20) NOT NULL,
    wording VARCHAR(500) NOT NULL,
    code VARCHAR(30) NOT NULL,
    term VARCHAR(500) NOT NULL,
    version VARCHAR(50),
    added_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (dictionary, wording)
);

-- Coding of each reported item on a case (the reported wording is never changed)
CREATE TABLE IF NOT EXISTS pv.term_codings (
    coding_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES pv.safety_cases(case_id) ON DELETE CASCADE,
    entity VARCHAR(30) NOT NULL,
    entity_id BIGINT NOT NULL,
    verbatim TEXT NOT NULL,
    dictionary VARCHAR(20),
    code VARCHAR(30),
    term VARCHAR(500),
    pt_code VARCHAR(10),
    pt_name VARCHAR(255),
    soc_code VARCHAR(10),
    soc_name VARCHAR(255),
    version VARCHAR(50),
    method VARCHAR(30),
    status VARCHAR(20) NOT NULL DEFAULT 'Needs review',
    message TEXT,
    candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
    coded_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    coded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    reviewed_at TIMESTAMPTZ,
    UNIQUE (entity, entity_id),
    CONSTRAINT term_codings_status_check CHECK (status IN ('Coded', 'Needs review', 'Unavailable', 'Not coded'))
);
CREATE INDEX IF NOT EXISTS idx_term_codings_case ON pv.term_codings (case_id);

CREATE TABLE IF NOT EXISTS pv.term_coding_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    coding_id BIGINT REFERENCES pv.term_codings(coding_id) ON DELETE CASCADE,
    case_id BIGINT,
    action VARCHAR(60) NOT NULL,
    snapshot JSONB NOT NULL,
    actor BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMIT;
