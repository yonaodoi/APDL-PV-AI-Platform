BEGIN;

CREATE TABLE IF NOT EXISTS pv.rsi_reactions (
    reaction_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    rsi_id BIGINT NOT NULL
        REFERENCES pv.reference_safety_information(rsi_id)
        ON DELETE CASCADE,

    reaction_term VARCHAR(500) NOT NULL,
    source_excerpt TEXT,

    review_status VARCHAR(30) NOT NULL DEFAULT 'Proposed' CHECK (
        review_status IN ('Proposed', 'Verified', 'Excluded')
    ),

    reviewed_by BIGINT
        REFERENCES pv.users(user_id)
        ON DELETE SET NULL,

    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (rsi_id, reaction_term)
);

CREATE INDEX IF NOT EXISTS idx_rsi_reactions_review
    ON pv.rsi_reactions (rsi_id, review_status);

COMMIT;