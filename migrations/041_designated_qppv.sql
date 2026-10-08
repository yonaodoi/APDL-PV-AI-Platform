BEGIN;

-- The designated QPPV may approve and finalise PSURs whatever their system
-- role, so an administrator who is also the QPPV keeps administration access.
ALTER TABLE pv.users
    ADD COLUMN IF NOT EXISTS is_designated_qppv BOOLEAN NOT NULL DEFAULT FALSE;

-- Yona Odoi is APDL's QPPV.
UPDATE pv.users
SET is_designated_qppv = TRUE
WHERE LOWER(TRIM(full_name)) = 'yona odoi';

COMMIT;
