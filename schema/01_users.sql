CREATE TABLE IF NOT EXISTS user_roles (
    slack_user_id  VARCHAR(50) PRIMARY KEY,
    display_name   VARCHAR(100),
    email          VARCHAR(255),
    roles          TEXT[] NOT NULL DEFAULT '{}',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
