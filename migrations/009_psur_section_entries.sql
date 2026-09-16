BEGIN;

CREATE TABLE pv.psur_section_entries (
    section_entry_id BIGSERIAL PRIMARY KEY,
    psur_id BIGINT NOT NULL REFERENCES pv.psur_reports(psur_id)
        ON DELETE CASCADE,

    section_key VARCHAR(100) NOT NULL,
    section_title VARCHAR(300) NOT NULL,
    content TEXT NOT NULL,

    updated_by BIGINT REFERENCES pv.users(user_id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT psur_section_entries_unique
        UNIQUE (psur_id, section_key)
);

CREATE INDEX psur_section_entries_psur_idx
    ON pv.psur_section_entries(psur_id);

COMMIT;