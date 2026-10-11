-- Document intake: one uploaded document may hold several patient reports
-- or follow-up information for existing cases. The original is kept, each
-- candidate patient report becomes a proposal a reviewer confirms, splits,
-- merges or rejects, and only confirmed proposals become cases.
BEGIN;

CREATE TABLE IF NOT EXISTS pv.intake_documents (
    document_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    original_filename VARCHAR(255) NOT NULL,
    stored_filename VARCHAR(255) NOT NULL UNIQUE,
    content_type VARCHAR(150),
    file_size_bytes BIGINT NOT NULL DEFAULT 0,
    file_sha256 VARCHAR(64) NOT NULL UNIQUE,      -- the same file is never processed twice
    page_count INTEGER,
    status VARCHAR(30) NOT NULL DEFAULT 'Queued'
        CHECK (status IN ('Queued', 'Reading', 'Finding patients', 'Extracting', 'Ready', 'Failed')),
    progress TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    uploaded_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS pv.intake_proposals (
    proposal_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES pv.intake_documents(document_id) ON DELETE CASCADE,
    seq INTEGER NOT NULL DEFAULT 1,
    kind VARCHAR(30) NOT NULL DEFAULT 'New patient'
        CHECK (kind IN ('New patient', 'Follow-up', 'Not a patient report')),
    status VARCHAR(20) NOT NULL DEFAULT 'Proposed'
        CHECK (status IN ('Proposed', 'Rejected', 'Merged', 'Created', 'Linked')),
    label VARCHAR(255),
    pages INTEGER[] NOT NULL DEFAULT '{}',
    excerpt TEXT,
    excerpt_found BOOLEAN NOT NULL DEFAULT FALSE,
    segment_text TEXT NOT NULL DEFAULT '',
    extraction_status VARCHAR(20) NOT NULL DEFAULT 'Pending'
        CHECK (extraction_status IN ('Pending', 'Done', 'Failed')),
    extraction_error TEXT,
    extracted JSONB NOT NULL DEFAULT '{}'::jsonb,
    flags JSONB NOT NULL DEFAULT '[]'::jsonb,
    duplicate_cases JSONB NOT NULL DEFAULT '[]'::jsonb,
    follow_up_reference VARCHAR(100),
    follow_up_case_id BIGINT REFERENCES pv.safety_cases(case_id) ON DELETE SET NULL,
    created_case_id BIGINT REFERENCES pv.safety_cases(case_id) ON DELETE SET NULL,
    merged_into BIGINT REFERENCES pv.intake_proposals(proposal_id) ON DELETE SET NULL,
    decision_note TEXT,
    decided_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    decided_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_intake_proposals_document ON pv.intake_proposals (document_id, seq);
-- A proposal can become at most one case, and a case comes from at most one proposal.
CREATE UNIQUE INDEX IF NOT EXISTS idx_intake_proposals_case ON pv.intake_proposals (created_case_id) WHERE created_case_id IS NOT NULL;

ALTER TABLE pv.safety_cases
    ADD COLUMN IF NOT EXISTS intake_document_id BIGINT REFERENCES pv.intake_documents(document_id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS intake_pages VARCHAR(100);

COMMIT;
