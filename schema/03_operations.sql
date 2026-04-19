CREATE TABLE IF NOT EXISTS pending_operations (
    tracking_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    initiated_by     VARCHAR(50) NOT NULL,
    action           VARCHAR(50) NOT NULL,
    payload          JSONB NOT NULL,
    status           VARCHAR(30) NOT NULL DEFAULT 'PENDING',
    approval_msg_ts  VARCHAR(50),
    approval_channel VARCHAR(50),
    approved_by      VARCHAR(50),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at       TIMESTAMPTZ NOT NULL DEFAULT NOW() + INTERVAL '15 minutes',
    completed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_ops_status    ON pending_operations (status);
CREATE INDEX IF NOT EXISTS idx_ops_initiator ON pending_operations (initiated_by);
