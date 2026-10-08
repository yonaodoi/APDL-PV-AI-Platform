BEGIN;

-- Automatic follow-up: tell requests from reminders, and manual sends from
-- the ones the system made on its own.
ALTER TABLE pv.case_follow_up_email_deliveries
    ADD COLUMN IF NOT EXISTS kind VARCHAR(20) NOT NULL DEFAULT 'Request',
    ADD COLUMN IF NOT EXISTS automatic BOOLEAN NOT NULL DEFAULT FALSE;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'case_follow_up_email_deliveries_kind_check'
    ) THEN
        ALTER TABLE pv.case_follow_up_email_deliveries
            ADD CONSTRAINT case_follow_up_email_deliveries_kind_check
            CHECK (kind IN ('Request', 'Reminder'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_follow_up_email_deliveries_case
    ON pv.case_follow_up_email_deliveries(case_id, sent_at DESC);

COMMIT;
