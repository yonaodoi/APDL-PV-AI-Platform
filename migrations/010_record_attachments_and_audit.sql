BEGIN;

CREATE TABLE IF NOT EXISTS pv.record_attachments (
    attachment_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    record_type VARCHAR(30) NOT NULL CHECK (
        record_type IN ('case', 'complaint', 'signal', 'psur')
    ),

    record_id BIGINT NOT NULL,

    original_filename VARCHAR(255) NOT NULL,
    stored_filename VARCHAR(255) NOT NULL UNIQUE,
    content_type VARCHAR(150),
    file_size_bytes BIGINT NOT NULL CHECK (file_size_bytes >= 0),

    uploaded_by BIGINT
        REFERENCES pv.users(user_id)
        ON DELETE SET NULL,

    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_record_attachments_record
    ON pv.record_attachments (record_type, record_id);

CREATE TABLE IF NOT EXISTS pv.audit_log (
    audit_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    record_type VARCHAR(30) NOT NULL,
    record_id BIGINT,
    action VARCHAR(100) NOT NULL,
    details TEXT,

    actor_user_id BIGINT
        REFERENCES pv.users(user_id)
        ON DELETE SET NULL,

    occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_audit_log_record
    ON pv.audit_log (record_type, record_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_log_actor
    ON pv.audit_log (actor_user_id, occurred_at DESC);

COMMIT;