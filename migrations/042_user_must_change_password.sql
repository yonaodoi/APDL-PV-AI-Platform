BEGIN;

-- Users given a temporary password (new accounts, or a reset by an
-- administrator) must choose their own password at the next sign-in.
ALTER TABLE pv.users
    ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE;

COMMIT;
