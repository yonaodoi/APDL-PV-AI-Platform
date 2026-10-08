BEGIN;

-- Listedness is checked automatically during case processing. Record
-- whether the saved assessment came from the automatic check or was
-- confirmed by a reviewer (the automatic check never overwrites a
-- reviewer's conclusion), whether it is provisional, and which reference
-- document it used.
ALTER TABLE pv.case_safety_assessments
    ADD COLUMN IF NOT EXISTS assessment_source VARCHAR(20) NOT NULL DEFAULT 'reviewer',
    ADD COLUMN IF NOT EXISTS is_provisional BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS reference_title TEXT;

ALTER TABLE pv.case_safety_assessments
    DROP CONSTRAINT IF EXISTS chk_case_safety_assessments_source;
ALTER TABLE pv.case_safety_assessments
    ADD CONSTRAINT chk_case_safety_assessments_source
    CHECK (assessment_source IN ('automatic', 'reviewer'));

COMMIT;
