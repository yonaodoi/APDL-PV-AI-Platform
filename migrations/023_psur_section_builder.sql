BEGIN;

CREATE TABLE IF NOT EXISTS pv.psur_builders (
    psur_builder_id BIGSERIAL PRIMARY KEY,

    psur_id BIGINT NOT NULL UNIQUE
        REFERENCES pv.psur_reports(psur_id)
        ON DELETE CASCADE,

    created_by BIGINT
        REFERENCES pv.users(user_id)
        ON DELETE SET NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pv.psur_builder_sections (
    psur_builder_section_id BIGSERIAL PRIMARY KEY,

    psur_builder_id BIGINT NOT NULL
        REFERENCES pv.psur_builders(psur_builder_id)
        ON DELETE CASCADE,

    section_key VARCHAR(100) NOT NULL,
    section_title VARCHAR(300) NOT NULL,
    section_order INTEGER NOT NULL,

    evidence_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    generated_content TEXT,
    final_content TEXT,

    updated_by BIGINT
        REFERENCES pv.users(user_id)
        ON DELETE SET NULL,

    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT psur_builder_sections_unique
        UNIQUE (psur_builder_id, section_key)
);

CREATE TABLE IF NOT EXISTS pv.psur_builder_ai_proposals (
    proposal_id BIGSERIAL PRIMARY KEY,

    psur_builder_section_id BIGINT NOT NULL
        REFERENCES pv.psur_builder_sections(
            psur_builder_section_id
        )
        ON DELETE CASCADE,

    proposal_type VARCHAR(30) NOT NULL
        CHECK (proposal_type IN ('suggestion', 'paraphrase')),

    proposed_content TEXT NOT NULL,
    evidence_used JSONB NOT NULL DEFAULT '[]'::jsonb,

    decision VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (decision IN ('pending', 'accepted', 'rejected')),

    created_by BIGINT
        REFERENCES pv.users(user_id)
        ON DELETE SET NULL,

    decided_by BIGINT
        REFERENCES pv.users(user_id)
        ON DELETE SET NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS psur_builders_psur_idx
    ON pv.psur_builders(psur_id);

CREATE INDEX IF NOT EXISTS psur_builder_sections_builder_idx
    ON pv.psur_builder_sections(psur_builder_id, section_order);

CREATE INDEX IF NOT EXISTS psur_builder_proposals_section_idx
    ON pv.psur_builder_ai_proposals(
        psur_builder_section_id,
        decision
    );

COMMIT;