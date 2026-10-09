-- One line of case stages owned by the team:
-- New -> Triage -> Follow-up requested -> Assessment -> QPPV review
--   -> Case approval -> Approved -> Submitted -> Closed.
-- "Medical review" becomes "Assessment"; the sign-off steps become stages.
-- Each case gets an owner (the PV officer who entered it).

BEGIN;

ALTER TABLE pv.safety_cases
    ADD COLUMN IF NOT EXISTS assigned_to INTEGER REFERENCES pv.users(user_id);

UPDATE pv.safety_cases
SET assigned_to = created_by
WHERE assigned_to IS NULL;

CREATE INDEX IF NOT EXISTS idx_safety_cases_assigned_to
    ON pv.safety_cases(assigned_to);

-- A new case belongs to whoever entered it, unless an owner is given.
CREATE OR REPLACE FUNCTION pv.set_case_owner() RETURNS trigger AS $$
BEGIN
    IF NEW.assigned_to IS NULL THEN
        NEW.assigned_to := NEW.created_by;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_set_case_owner ON pv.safety_cases;
CREATE TRIGGER trg_set_case_owner
    BEFORE INSERT ON pv.safety_cases
    FOR EACH ROW EXECUTE FUNCTION pv.set_case_owner();

-- Cases already in sign-off take the matching stage.
UPDATE pv.safety_cases
SET workflow_status = CASE approval_stage
        WHEN 'Pending review' THEN 'QPPV review'
        WHEN 'Pending approval' THEN 'Case approval'
        WHEN 'Approved' THEN 'Approved'
        ELSE 'Assessment'
    END
WHERE workflow_status IN ('Medical review', 'Ready for submission')
   OR (approval_stage IN ('Pending review', 'Pending approval', 'Approved')
       AND workflow_status NOT IN ('Submitted', 'Closed')
       AND regulatory_submitted_date IS NULL);

COMMIT;
