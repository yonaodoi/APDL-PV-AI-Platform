BEGIN;

CREATE TABLE IF NOT EXISTS pv.case_duplicate_reviews (
    duplicate_review_id BIGSERIAL PRIMARY KEY,
    case_id_low BIGINT NOT NULL
        REFERENCES pv.safety_cases(case_id) ON DELETE CASCADE,
    case_id_high BIGINT NOT NULL
        REFERENCES pv.safety_cases(case_id) ON DELETE CASCADE,
    review_status VARCHAR(30) NOT NULL
        CHECK (review_status IN ('Confirmed duplicate', 'Not a duplicate')),
    reviewed_by BIGINT NOT NULL REFERENCES pv.users(user_id),
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (case_id_low < case_id_high),
    UNIQUE (case_id_low, case_id_high)
);

COMMIT;
