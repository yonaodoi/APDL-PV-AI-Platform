BEGIN;

ALTER TABLE pv.case_safety_assessments
    ADD COLUMN IF NOT EXISTS frequency_assessment VARCHAR(80);

ALTER TABLE pv.case_safety_assessments
    ADD COLUMN IF NOT EXISTS frequency_evidence TEXT;

ALTER TABLE pv.case_safety_assessments
    ADD CONSTRAINT case_safety_assessments_frequency_check
    CHECK (
        frequency_assessment IS NULL
        OR frequency_assessment IN (
            'Very common',
            'Common',
            'Uncommon',
            'Rare',
            'Very rare',
            'Frequency not known',
            'Not stated',
            'Not assessable'
        )
    );

COMMIT;