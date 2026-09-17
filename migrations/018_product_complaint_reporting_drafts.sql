CREATE TABLE IF NOT EXISTS pv.product_complaint_reporting_drafts (
    draft_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filter_state JSONB NOT NULL DEFAULT '{}'::jsonb,
    report_content JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by BIGINT REFERENCES pv.users(user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS
    idx_product_complaint_reporting_drafts_created_by
ON pv.product_complaint_reporting_drafts(created_by);

CREATE INDEX IF NOT EXISTS
    idx_product_complaint_reporting_drafts_updated_at
ON pv.product_complaint_reporting_drafts(updated_at DESC);