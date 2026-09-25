BEGIN;

CREATE TABLE IF NOT EXISTS pv.case_follow_up_reminders (
    reminder_id BIGSERIAL PRIMARY KEY,
    task_id BIGINT NOT NULL
        REFERENCES pv.case_follow_up_tasks(task_id) ON DELETE CASCADE,
    reminder_date DATE NOT NULL DEFAULT CURRENT_DATE,
    message TEXT NOT NULL,
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by BIGINT REFERENCES pv.users(user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (task_id, reminder_date)
);

CREATE INDEX IF NOT EXISTS idx_case_follow_up_reminders_open
    ON pv.case_follow_up_reminders(acknowledged_at, created_at DESC);

COMMIT;
