BEGIN;

ALTER TABLE pv.psur_builders
    ADD COLUMN IF NOT EXISTS rsi_id BIGINT
        REFERENCES pv.reference_safety_information(rsi_id)
        ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS psur_builders_rsi_idx
    ON pv.psur_builders(rsi_id);

COMMIT;