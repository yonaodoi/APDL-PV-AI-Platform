BEGIN;

-- Three-level sign-off for safety cases:
-- PV Officer prepares -> QPPV / Deputy QPPV reviews -> Group Head approves.
INSERT INTO pv.roles (role_name, description)
VALUES (
    'Group Head RA & Quality',
    'Approves safety case reports before regulatory submission'
)
ON CONFLICT (role_name) DO NOTHING;

ALTER TABLE pv.safety_cases
    ADD COLUMN IF NOT EXISTS approval_stage VARCHAR(30),
    ADD COLUMN IF NOT EXISTS approval_updated_at TIMESTAMPTZ;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'safety_cases_approval_stage_check'
    ) THEN
        ALTER TABLE pv.safety_cases
            ADD CONSTRAINT safety_cases_approval_stage_check
            CHECK (approval_stage IS NULL OR approval_stage IN (
                'Pending review', 'Pending approval', 'Approved', 'Returned'
            ));
    END IF;
END $$;

-- Every step of the sign-off, in order.
CREATE TABLE IF NOT EXISTS pv.case_approvals (
    approval_id BIGSERIAL PRIMARY KEY,
    case_id BIGINT NOT NULL
        REFERENCES pv.safety_cases(case_id) ON DELETE CASCADE,
    step VARCHAR(30) NOT NULL
        CHECK (step IN ('Sent for review', 'Reviewed', 'Approved', 'Returned', 'Reset')),
    comment TEXT,
    decided_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_case_approvals_case
    ON pv.case_approvals(case_id, decided_at DESC);

-- Daily summary emails already sent, so each person gets at most one a day,
-- and urgent alerts already sent for a case at a stage.
CREATE TABLE IF NOT EXISTS pv.approval_notifications (
    notification_id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES pv.users(user_id) ON DELETE CASCADE,
    kind VARCHAR(30) NOT NULL,
    notice_key VARCHAR(100) NOT NULL,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, kind, notice_key)
);

-- Settings an administrator can change from the Administration module.
CREATE TABLE IF NOT EXISTS pv.app_settings (
    setting_key VARCHAR(100) PRIMARY KEY,
    value JSONB NOT NULL,
    updated_by BIGINT REFERENCES pv.users(user_id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMIT;
