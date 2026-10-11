-- ICSR data structure (sections A–I) and the case processing pathway.
-- Everything is added alongside the existing columns, which stay in use;
-- existing case data is copied into the new repeatable sections once.
BEGIN;

-- A. Administrative / B. Patient / I. Summary / pathway --------------------
ALTER TABLE pv.safety_cases
    ADD COLUMN IF NOT EXISTS icsr_report_type VARCHAR(40),
    ADD COLUMN IF NOT EXISTS most_recent_info_date DATE,
    ADD COLUMN IF NOT EXISTS expedited_criteria VARCHAR(20),
    ADD COLUMN IF NOT EXISTS expedited_jurisdiction VARCHAR(150),
    ADD COLUMN IF NOT EXISTS expedited_basis TEXT,
    ADD COLUMN IF NOT EXISTS reporter_qualification VARCHAR(60),
    ADD COLUMN IF NOT EXISTS reporter_country_id INTEGER REFERENCES pv.countries(country_id),
    ADD COLUMN IF NOT EXISTS reporter_title VARCHAR(50),
    ADD COLUMN IF NOT EXISTS reporter_given_name VARCHAR(100),
    ADD COLUMN IF NOT EXISTS reporter_middle_name VARCHAR(100),
    ADD COLUMN IF NOT EXISTS reporter_family_name VARCHAR(100),
    ADD COLUMN IF NOT EXISTS patient_dob_text VARCHAR(10),
    ADD COLUMN IF NOT EXISTS patient_age_value NUMERIC(7, 2),
    ADD COLUMN IF NOT EXISTS patient_age_group VARCHAR(30),
    ADD COLUMN IF NOT EXISTS gestation_period NUMERIC(6, 2),
    ADD COLUMN IF NOT EXISTS gestation_unit VARCHAR(20),
    ADD COLUMN IF NOT EXISTS patient_weight_value NUMERIC(7, 2),
    ADD COLUMN IF NOT EXISTS patient_weight_unit VARCHAR(10),
    ADD COLUMN IF NOT EXISTS patient_height_value NUMERIC(7, 2),
    ADD COLUMN IF NOT EXISTS patient_height_unit VARCHAR(10),
    ADD COLUMN IF NOT EXISTS patient_lmp_text VARCHAR(10),
    ADD COLUMN IF NOT EXISTS patient_ethnicity VARCHAR(100),
    ADD COLUMN IF NOT EXISTS patient_died VARCHAR(10),
    ADD COLUMN IF NOT EXISTS death_date_text VARCHAR(10),
    ADD COLUMN IF NOT EXISTS death_reported_causes TEXT,
    ADD COLUMN IF NOT EXISTS autopsy_done VARCHAR(10),
    ADD COLUMN IF NOT EXISTS autopsy_causes TEXT,
    ADD COLUMN IF NOT EXISTS parent_child_report VARCHAR(10),
    ADD COLUMN IF NOT EXISTS medical_history_reported VARCHAR(10),
    ADD COLUMN IF NOT EXISTS reporter_comments TEXT,
    ADD COLUMN IF NOT EXISTS company_diagnosis TEXT,
    ADD COLUMN IF NOT EXISTS company_comments TEXT,
    ADD COLUMN IF NOT EXISTS linked_case_refs TEXT,
    ADD COLUMN IF NOT EXISTS case_category VARCHAR(30),
    ADD COLUMN IF NOT EXISTS reporting_channel VARCHAR(100),
    ADD COLUMN IF NOT EXISTS reporting_partner VARCHAR(200),
    ADD COLUMN IF NOT EXISTS study_identifier VARCHAR(150),
    ADD COLUMN IF NOT EXISTS literature_reference TEXT,
    ADD COLUMN IF NOT EXISTS null_flavours JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- minimum case validity (separate from completeness)
    ADD COLUMN IF NOT EXISTS validity_patient VARCHAR(20),
    ADD COLUMN IF NOT EXISTS validity_reporter VARCHAR(20),
    ADD COLUMN IF NOT EXISTS validity_product VARCHAR(20),
    ADD COLUMN IF NOT EXISTS validity_event VARCHAR(20),
    ADD COLUMN IF NOT EXISTS validity_decision VARCHAR(30),
    ADD COLUMN IF NOT EXISTS validity_notes TEXT,
    ADD COLUMN IF NOT EXISTS validity_decided_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS validity_decided_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS icsr_started_at TIMESTAMPTZ;

-- Additional primary sources (the first reporter stays on the case row) ----
CREATE TABLE IF NOT EXISTS pv.case_reporters (
    case_reporter_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES pv.safety_cases(case_id) ON DELETE CASCADE,
    qualification VARCHAR(60),
    country_id INTEGER REFERENCES pv.countries(country_id),
    title VARCHAR(50),
    given_name VARCHAR(100),
    middle_name VARCHAR(100),
    family_name VARCHAR(100),
    organisation VARCHAR(200),
    email VARCHAR(255),
    phone VARCHAR(100),
    null_flavours JSONB NOT NULL DEFAULT '{}'::jsonb,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_case_reporters_case ON pv.case_reporters(case_id);

-- Parent (parent–child reports) -------------------------------------------
CREATE TABLE IF NOT EXISTS pv.case_parents (
    case_id BIGINT PRIMARY KEY REFERENCES pv.safety_cases(case_id) ON DELETE CASCADE,
    identification VARCHAR(100),
    dob_text VARCHAR(10),
    age_value NUMERIC(7, 2),
    age_unit VARCHAR(20),
    lmp_text VARCHAR(10),
    sex VARCHAR(20),
    weight_value NUMERIC(7, 2),
    weight_unit VARCHAR(10),
    height_value NUMERIC(7, 2),
    height_unit VARCHAR(10),
    medical_history_text TEXT,
    past_drugs_text TEXT,
    null_flavours JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- C. Drugs: extra fields on the existing product rows ---------------------
ALTER TABLE pv.case_products
    ADD COLUMN IF NOT EXISTS brand_name VARCHAR(255),
    ADD COLUMN IF NOT EXISTS dose_value NUMERIC(12, 4),
    ADD COLUMN IF NOT EXISTS dose_unit VARCHAR(50),
    ADD COLUMN IF NOT EXISTS start_text VARCHAR(10),
    ADD COLUMN IF NOT EXISTS end_text VARCHAR(10),
    ADD COLUMN IF NOT EXISTS null_flavours JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0;

-- D. Reactions/events ------------------------------------------------------
CREATE TABLE IF NOT EXISTS pv.case_reactions (
    reaction_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES pv.safety_cases(case_id) ON DELETE CASCADE,
    reported_term TEXT NOT NULL,
    serious VARCHAR(10),
    crit_death BOOLEAN NOT NULL DEFAULT FALSE,
    crit_life_threatening BOOLEAN NOT NULL DEFAULT FALSE,
    crit_hospitalisation BOOLEAN NOT NULL DEFAULT FALSE,
    crit_disability BOOLEAN NOT NULL DEFAULT FALSE,
    crit_congenital_anomaly BOOLEAN NOT NULL DEFAULT FALSE,
    crit_other_medically_important BOOLEAN NOT NULL DEFAULT FALSE,
    outcome VARCHAR(60),
    medically_confirmed VARCHAR(10),
    start_text VARCHAR(10),
    end_text VARCHAR(10),
    duration_value NUMERIC(8, 2),
    duration_unit VARCHAR(20),
    country_id INTEGER REFERENCES pv.countries(country_id),
    null_flavours JSONB NOT NULL DEFAULT '{}'::jsonb,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_case_reactions_case ON pv.case_reactions(case_id);

-- Drug–reaction relationship (dechallenge, rechallenge, intervals) --------
CREATE TABLE IF NOT EXISTS pv.case_drug_reactions (
    case_drug_reaction_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES pv.safety_cases(case_id) ON DELETE CASCADE,
    case_product_id BIGINT NOT NULL REFERENCES pv.case_products(case_product_id) ON DELETE CASCADE,
    reaction_id BIGINT NOT NULL REFERENCES pv.case_reactions(reaction_id) ON DELETE CASCADE,
    start_interval_value NUMERIC(8, 2),
    start_interval_unit VARCHAR(20),
    last_dose_interval_value NUMERIC(8, 2),
    last_dose_interval_unit VARCHAR(20),
    dechallenge VARCHAR(40),
    rechallenge VARCHAR(40),
    UNIQUE (case_product_id, reaction_id)
);

-- E. Structured medical history --------------------------------------------
CREATE TABLE IF NOT EXISTS pv.case_medical_history (
    history_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES pv.safety_cases(case_id) ON DELETE CASCADE,
    for_parent BOOLEAN NOT NULL DEFAULT FALSE,
    condition_text TEXT NOT NULL,
    start_text VARCHAR(10),
    end_text VARCHAR(10),
    continuing VARCHAR(10),
    comments TEXT,
    family_history BOOLEAN NOT NULL DEFAULT FALSE,
    null_flavours JSONB NOT NULL DEFAULT '{}'::jsonb,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_case_medical_history_case ON pv.case_medical_history(case_id);

-- F. Tests and investigations ----------------------------------------------
CREATE TABLE IF NOT EXISTS pv.case_tests (
    test_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES pv.safety_cases(case_id) ON DELETE CASCADE,
    test_name TEXT NOT NULL,
    test_date_text VARCHAR(10),
    result_text TEXT,
    result_unit VARCHAR(50),
    normal_low VARCHAR(50),
    normal_high VARCHAR(50),
    comments TEXT,
    more_info_available VARCHAR(10),
    null_flavours JSONB NOT NULL DEFAULT '{}'::jsonb,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_case_tests_case ON pv.case_tests(case_id);

-- G. Relevant past drug history --------------------------------------------
CREATE TABLE IF NOT EXISTS pv.case_past_drugs (
    past_drug_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES pv.safety_cases(case_id) ON DELETE CASCADE,
    for_parent BOOLEAN NOT NULL DEFAULT FALSE,
    drug_name TEXT NOT NULL,
    indication_text TEXT,
    reaction_text TEXT,
    start_text VARCHAR(10),
    end_text VARCHAR(10),
    null_flavours JSONB NOT NULL DEFAULT '{}'::jsonb,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_case_past_drugs_case ON pv.case_past_drugs(case_id);

-- H. Causality assessments (per drug–reaction pair, repeatable) -----------
CREATE TABLE IF NOT EXISTS pv.case_causality (
    causality_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES pv.safety_cases(case_id) ON DELETE CASCADE,
    case_product_id BIGINT REFERENCES pv.case_products(case_product_id) ON DELETE CASCADE,
    reaction_id BIGINT REFERENCES pv.case_reactions(reaction_id) ON DELETE CASCADE,
    method VARCHAR(100),
    source VARCHAR(60),
    result VARCHAR(100),
    comments TEXT,
    assessor VARCHAR(200),
    assessed_on DATE,
    is_ai_draft BOOLEAN NOT NULL DEFAULT FALSE,
    null_flavours JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_case_causality_case ON pv.case_causality(case_id);
ALTER TABLE pv.case_causality ADD COLUMN IF NOT EXISTS null_flavours JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Copy existing information into the new structure (once) ----------------
UPDATE pv.safety_cases
SET patient_age_value = COALESCE(patient_age_value, patient_age_years),
    patient_weight_value = COALESCE(patient_weight_value, patient_weight_kg),
    patient_weight_unit = COALESCE(patient_weight_unit, CASE WHEN patient_weight_kg IS NOT NULL THEN 'kg' END),
    patient_dob_text = COALESCE(patient_dob_text, to_char(patient_date_of_birth, 'YYYY-MM-DD')),
    case_category = COALESCE(case_category, CASE WHEN source = 'Literature' THEN 'Literature' ELSE 'Spontaneous' END),
    icsr_report_type = COALESCE(icsr_report_type, CASE WHEN source = 'Literature' THEN 'Other' ELSE 'Spontaneous report' END),
    medical_history_reported = COALESCE(medical_history_reported, CASE WHEN NULLIF(TRIM(medical_history), '') IS NOT NULL THEN 'Yes' END);

UPDATE pv.case_products
SET start_text = COALESCE(start_text, to_char(therapy_start_date, 'YYYY-MM-DD')),
    end_text = COALESCE(end_text, to_char(therapy_end_date, 'YYYY-MM-DD'));

INSERT INTO pv.case_reactions (
    case_id, reported_term, serious, outcome, start_text, end_text, country_id,
    crit_death, crit_life_threatening, crit_hospitalisation, crit_disability,
    crit_congenital_anomaly, crit_other_medically_important
)
SELECT c.case_id,
       c.event_description,
       CASE WHEN c.seriousness THEN 'Yes' ELSE 'No' END,
       c.event_outcome,
       to_char(c.event_onset_date, 'YYYY-MM-DD'),
       to_char(c.event_end_date, 'YYYY-MM-DD'),
       c.country_id,
       c.seriousness AND (c.event_outcome = 'Fatal' OR c.seriousness_criteria ILIKE '%death%' OR c.seriousness_criteria ILIKE '%fatal%' OR c.seriousness_criteria ILIKE '%died%'),
       c.seriousness AND c.seriousness_criteria ILIKE '%life%threat%',
       c.seriousness AND (c.seriousness_criteria ILIKE '%hospital%' OR c.seriousness_criteria ILIKE '%admi%'),
       c.seriousness AND (c.seriousness_criteria ILIKE '%disab%' OR c.seriousness_criteria ILIKE '%incapac%'),
       c.seriousness AND c.seriousness_criteria ILIKE '%congenital%',
       c.seriousness AND c.seriousness_criteria ILIKE '%medically important%'
FROM pv.safety_cases AS c
WHERE NULLIF(TRIM(c.event_description), '') IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM pv.case_reactions r WHERE r.case_id = c.case_id);

-- The single causality value recorded so far becomes the company assessment
-- of the first suspect drug against the first reaction.
INSERT INTO pv.case_causality (case_id, case_product_id, reaction_id, method, source, result, comments, assessed_on)
SELECT c.case_id,
       (SELECT p.case_product_id FROM pv.case_products p WHERE p.case_id = c.case_id ORDER BY p.case_product_id LIMIT 1),
       (SELECT r.reaction_id FROM pv.case_reactions r WHERE r.case_id = c.case_id ORDER BY r.reaction_id LIMIT 1),
       'WHO-UMC',
       'Company',
       c.causality_assessment,
       'Copied from the causality value recorded before the ICSR form existed.',
       c.updated_at::date
FROM pv.safety_cases AS c
WHERE NULLIF(TRIM(c.causality_assessment), '') IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM pv.case_causality k WHERE k.case_id = c.case_id);

-- Existing cases were already being processed: mark validity as decided
-- from the existing minimum-criteria checks so they are not sent back to
-- intake. New cases go through the validity step.
UPDATE pv.safety_cases AS c
SET validity_decision = 'Valid',
    validity_decided_at = COALESCE(c.created_at, CURRENT_TIMESTAMP),
    validity_notes = 'Recorded as valid when the case pathway was introduced (existing case).',
    icsr_started_at = COALESCE(c.created_at, CURRENT_TIMESTAMP)
WHERE c.validity_decision IS NULL
  AND c.workflow_status NOT IN ('New', 'Follow-up requested');

COMMIT;
