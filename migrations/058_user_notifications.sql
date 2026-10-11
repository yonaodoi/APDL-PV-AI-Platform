-- In-app notifications: "your AI assessment is ready", "your document has
-- been read", etc. Shown as a pop-up with a sound on whatever page the user
-- has open, and listed until seen.
BEGIN;

CREATE TABLE IF NOT EXISTS pv.user_notifications (
    notification_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES pv.users(user_id) ON DELETE CASCADE,
    kind VARCHAR(40) NOT NULL,
    tone VARCHAR(10) NOT NULL DEFAULT 'success' CHECK (tone IN ('success', 'error', 'info')),
    title VARCHAR(200) NOT NULL,
    message TEXT,
    link VARCHAR(500),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    delivered_at TIMESTAMPTZ,
    read_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_user_notifications_pending
    ON pv.user_notifications (user_id, delivered_at, created_at);

COMMIT;
