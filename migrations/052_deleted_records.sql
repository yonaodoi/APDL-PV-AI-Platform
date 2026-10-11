-- Permanent deletion of cases, signals, complaints and PSUR records by a
-- System Administrator. A full copy of the record and everything that
-- depended on it is kept here; the audit trail is never deleted.
BEGIN;

CREATE TABLE IF NOT EXISTS pv.deleted_records (
    deleted_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    record_type VARCHAR(30) NOT NULL,
    record_id BIGINT NOT NULL,
    record_number VARCHAR(255),
    reason TEXT NOT NULL,
    snapshot JSONB NOT NULL,
    deleted_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    deleted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_deleted_records_type
    ON pv.deleted_records (record_type, deleted_at DESC);

COMMIT;
