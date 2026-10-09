-- Reporters' email replies to follow-up requests, read from the sending
-- inbox. Each reply is linked to its case; its text and attachments are
-- stored as case attachments so the AI can suggest updates for review.

BEGIN;

CREATE TABLE IF NOT EXISTS pv.follow_up_replies (
    reply_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    message_id TEXT NOT NULL UNIQUE,
    case_id BIGINT REFERENCES pv.safety_cases(case_id) ON DELETE SET NULL,
    from_email VARCHAR(255) NOT NULL,
    from_name VARCHAR(255),
    subject TEXT,
    received_at TIMESTAMPTZ NOT NULL,
    reply_text TEXT,
    attachment_ids BIGINT[] NOT NULL DEFAULT '{}',
    match_note TEXT,
    status VARCHAR(20) NOT NULL DEFAULT 'New'
        CHECK (status IN ('New', 'Handled', 'Unmatched', 'Ignored')),
    handled_by BIGINT REFERENCES pv.users(user_id),
    handled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_follow_up_replies_case
    ON pv.follow_up_replies(case_id, received_at DESC);

CREATE INDEX IF NOT EXISTS idx_follow_up_replies_status
    ON pv.follow_up_replies(status);

COMMIT;
