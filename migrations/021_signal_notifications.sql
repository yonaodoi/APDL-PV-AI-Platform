BEGIN;

CREATE TABLE pv.safety_signal_notifications (
    notification_id BIGSERIAL PRIMARY KEY,
    signal_id BIGINT NOT NULL
        REFERENCES pv.safety_signals(signal_id)
        ON DELETE CASCADE,
    user_id BIGINT NOT NULL
        REFERENCES pv.users(user_id)
        ON DELETE CASCADE,
    message TEXT NOT NULL,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (signal_id, user_id)
);

CREATE INDEX safety_signal_notifications_user_idx
    ON pv.safety_signal_notifications (
        user_id,
        is_read,
        created_at DESC
    );

COMMIT;