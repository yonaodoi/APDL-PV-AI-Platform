UPDATE pv.product_complaints
SET severity = 'Serious'
WHERE severity = 'Critical';

UPDATE pv.product_complaints
SET status = CASE
    WHEN status IN ('New', 'Awaiting information')
        THEN 'Under investigation'
    WHEN status = 'Closed'
        THEN 'Investigation complete'
    ELSE status
END;

ALTER TABLE pv.product_complaints
    ALTER COLUMN status
    SET DEFAULT 'Under investigation';

ALTER TABLE pv.product_complaints
    ADD CONSTRAINT chk_product_complaints_severity
    CHECK (severity IN ('Non-serious', 'Serious'));

ALTER TABLE pv.product_complaints
    ADD CONSTRAINT chk_product_complaints_status
    CHECK (
        status IN (
            'Under investigation',
            'Investigation complete'
        )
    );