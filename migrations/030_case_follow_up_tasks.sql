BEGIN;

CREATE TABLE IF NOT EXISTS pv.case_follow_up_tasks (
    task_id BIGSERIAL PRIMARY KEY,
    case_id BIGINT NOT NULL
        REFERENCES pv.safety_cases(case_id) ON DELETE CASCADE,
    check_code VARCHAR(80),
    task_title VARCHAR(255) NOT NULL,
    task_description TEXT NOT NULL,
    assigned_to BIGINT REFERENCES pv.users(user_id),
    due_date DATE NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'Open'
        CHECK (status IN ('Open', 'In progress', 'Completed', 'Cancelled')),
    completed_at TIMESTAMPTZ,
    completed_by BIGINT REFERENCES pv.users(user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (case_id, check_code)
);

CREATE INDEX IF NOT EXISTS idx_case_follow_up_tasks_status_due
    ON pv.case_follow_up_tasks(status, due_date);

COMMIT;
