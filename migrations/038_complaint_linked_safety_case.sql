BEGIN;

-- A product complaint that reports an adverse event must be processed as a
-- safety case. This links the complaint to the case created from it.
ALTER TABLE pv.product_complaints
    ADD COLUMN IF NOT EXISTS linked_case_id BIGINT
        REFERENCES pv.safety_cases(case_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_product_complaints_linked_case
    ON pv.product_complaints(linked_case_id);

COMMIT;
