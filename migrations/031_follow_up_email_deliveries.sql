BEGIN;

CREATE TABLE IF NOT EXISTS pv.case_follow_up_email_deliveries (
    delivery_id BIGSERIAL PRIMARY KEY,
    task_id BIGINT NOT NULL
        REFERENCES pv.case_follow_up_tasks(task_id) ON DELETE CASCADE,
    case_id BIGINT NOT NULL
        REFERENCES pv.safety_cases(case_id) ON DELETE CASCADE,
    recipient_email VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL
        CHECK (status IN ('Sent', 'Failed')),
    error_message TEXT,
    sent_by BIGINT REFERENCES pv.users(user_id),
    sent_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_follow_up_email_deliveries_task
    ON pv.case_follow_up_email_deliveries(task_id, sent_at DESC);

COMMIT;
