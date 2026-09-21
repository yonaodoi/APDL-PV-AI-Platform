BEGIN;

ALTER TABLE pv.reference_safety_information
    ADD COLUMN IF NOT EXISTS extracted_text TEXT;

ALTER TABLE pv.reference_safety_information
    ADD COLUMN IF NOT EXISTS extraction_status VARCHAR(30)
    NOT NULL DEFAULT 'Not extracted'
    CHECK (
        extraction_status IN (
            'Not extracted',
            'Extracted',
            'Extraction failed'
        )
    );

ALTER TABLE pv.reference_safety_information
    ADD COLUMN IF NOT EXISTS extracted_at TIMESTAMPTZ;

COMMIT;