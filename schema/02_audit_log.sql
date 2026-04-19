CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGSERIAL PRIMARY KEY,
    tracking_id     UUID NOT NULL DEFAULT gen_random_uuid(),
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id         VARCHAR(50) NOT NULL,
    username        VARCHAR(100),
    channel_id      VARCHAR(50) NOT NULL,
    action          VARCHAR(50) NOT NULL,
    service         VARCHAR(100),
    environment     VARCHAR(50),
    version         VARCHAR(100),
    raw_command     TEXT NOT NULL,
    parsed_intent   JSONB,
    outcome         VARCHAR(20) NOT NULL,
    outcome_detail  TEXT,
    duration_ms     INTEGER,
    approved_by     VARCHAR(50)
);

CREATE INDEX IF NOT EXISTS idx_audit_user      ON audit_log (user_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log (timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_service   ON audit_log (service);
CREATE INDEX IF NOT EXISTS idx_audit_outcome   ON audit_log (outcome);
