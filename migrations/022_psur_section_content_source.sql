BEGIN;

ALTER TABLE pv.psur_section_entries
    ADD COLUMN content_source VARCHAR(30)
        NOT NULL DEFAULT 'manual';

ALTER TABLE pv.psur_section_entries
    ADD CONSTRAINT psur_section_entries_content_source_check
    CHECK (content_source IN ('system_draft', 'manual'));

COMMIT;