-- Controlled ICSR narratives: versioned, approved templates per case type,
-- and every narrative version kept with its template version, the facts it
-- was built from, the issues found and who reviewed it.
BEGIN;

-- Facts the narrative needs that had no structured field yet.
ALTER TABLE pv.safety_cases
    ADD COLUMN IF NOT EXISTS literature_authors TEXT,
    ADD COLUMN IF NOT EXISTS literature_journal VARCHAR(300),
    ADD COLUMN IF NOT EXISTS literature_year VARCHAR(10),
    ADD COLUMN IF NOT EXISTS literature_title TEXT,
    ADD COLUMN IF NOT EXISTS literature_doi VARCHAR(200),
    ADD COLUMN IF NOT EXISTS study_subject_id VARCHAR(100),
    ADD COLUMN IF NOT EXISTS study_blinded VARCHAR(10),
    ADD COLUMN IF NOT EXISTS patient_allergies TEXT,
    ADD COLUMN IF NOT EXISTS social_history TEXT;

CREATE TABLE IF NOT EXISTS pv.narrative_templates (
    template_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_type VARCHAR(30) NOT NULL CHECK (case_type IN ('Spontaneous', 'Clinical trial', 'Literature')),
    version INTEGER NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'Draft' CHECK (status IN ('Draft', 'Approved', 'Historic')),
    sections JSONB NOT NULL,
    change_note TEXT,
    created_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    approved_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    approved_at TIMESTAMPTZ,
    UNIQUE (case_type, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_narrative_templates_approved
    ON pv.narrative_templates (case_type) WHERE status = 'Approved';

CREATE TABLE IF NOT EXISTS pv.case_narratives (
    narrative_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id BIGINT NOT NULL REFERENCES pv.safety_cases(case_id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    narrative_text TEXT NOT NULL,
    source VARCHAR(30) NOT NULL CHECK (source IN ('Generated', 'Edited', 'ICSR form', 'Earlier narrative')),
    template_id BIGINT REFERENCES pv.narrative_templates(template_id) ON DELETE SET NULL,
    template_case_type VARCHAR(30),
    template_version INTEGER,
    sentences JSONB NOT NULL DEFAULT '[]'::jsonb,   -- text with the structured fields each sentence used
    issues JSONB NOT NULL DEFAULT '[]'::jsonb,      -- contradictions and exclusions found when generating
    status VARCHAR(20) NOT NULL DEFAULT 'Draft' CHECK (status IN ('Draft', 'Current', 'Reviewed', 'Superseded')),
    created_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    reviewed_at TIMESTAMPTZ,
    UNIQUE (case_id, version)
);
CREATE INDEX IF NOT EXISTS idx_case_narratives_case ON pv.case_narratives (case_id, version DESC);

COMMIT;
