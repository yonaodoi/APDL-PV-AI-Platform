ALTER TABLE pv.safety_signals
    ADD COLUMN auto_detected BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN auto_detection_key VARCHAR(600),
    ADD COLUMN last_screened_at TIMESTAMPTZ;

CREATE TABLE pv.safety_signal_cases (
    signal_case_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    signal_id BIGINT NOT NULL
        REFERENCES pv.safety_signals(signal_id)
        ON DELETE CASCADE,
    case_id BIGINT NOT NULL
        REFERENCES pv.safety_cases(case_id)
        ON DELETE CASCADE,
    linked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (signal_id, case_id)
);

CREATE UNIQUE INDEX
    ux_safety_signals_open_auto_detection_key
ON pv.safety_signals(auto_detection_key)
WHERE auto_detection_key IS NOT NULL
  AND status IN ('New', 'Under evaluation');

CREATE INDEX idx_safety_signal_cases_case
    ON pv.safety_signal_cases(case_id);

CREATE INDEX idx_safety_signal_cases_signal
    ON pv.safety_signal_cases(signal_id);