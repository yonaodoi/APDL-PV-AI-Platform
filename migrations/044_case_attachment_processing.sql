BEGIN;

-- Case attachments carry a document type, and documents that inform the
-- case (RSI, source reports, follow-up responses, clinical records) are
-- processed automatically. Suggested field updates wait for a reviewer.
ALTER TABLE pv.record_attachments
    ADD COLUMN IF NOT EXISTS document_type VARCHAR(40) NOT NULL DEFAULT 'other',
    ADD COLUMN IF NOT EXISTS processing_status VARCHAR(40),
    ADD COLUMN IF NOT EXISTS processing_note TEXT,
    ADD COLUMN IF NOT EXISTS suggested_updates JSONB,
    ADD COLUMN IF NOT EXISTS linked_rsi_id BIGINT
        REFERENCES pv.reference_safety_information(rsi_id) ON DELETE SET NULL;

COMMIT;
