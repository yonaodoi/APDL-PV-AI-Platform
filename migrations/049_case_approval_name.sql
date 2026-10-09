-- The approval stage is called "Case approval" (was "Group Head approval").
BEGIN;

UPDATE pv.safety_cases
SET workflow_status = 'Case approval'
WHERE workflow_status = 'Group Head approval';

COMMIT;
