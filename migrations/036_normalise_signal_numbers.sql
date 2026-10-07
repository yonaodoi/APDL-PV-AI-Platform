BEGIN;

-- Remove stray whitespace around hyphens in manually entered Signal IDs,
-- e.g. 'APDL -SIG-003' -> 'APDL-SIG-003'. Rows whose tidied ID would clash
-- with an existing ID are left unchanged for manual review.
UPDATE pv.safety_signals AS signals
SET signal_number = REGEXP_REPLACE(TRIM(signals.signal_number), '\s*-\s*', '-', 'g'),
    updated_at = CURRENT_TIMESTAMP
WHERE signals.signal_number <> REGEXP_REPLACE(TRIM(signals.signal_number), '\s*-\s*', '-', 'g')
  AND NOT EXISTS (
      SELECT 1
      FROM pv.safety_signals AS other
      WHERE other.signal_number
            = REGEXP_REPLACE(TRIM(signals.signal_number), '\s*-\s*', '-', 'g')
  );

COMMIT;
