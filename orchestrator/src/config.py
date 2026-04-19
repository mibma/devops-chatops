from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql://chatops:password@postgres:5432/chatops"
    redis_url: str = "redis://redis:6379/0"

    jenkins_url: str = "http://jenkins.your-company.com"
    jenkins_user: str = "chatops-bot"
    jenkins_token: str = ""

    slack_bot_token: str = ""

    kubeconfig: str | None = None
    in_cluster: bool = False

    permissions_file: str = "/app/config/permissions.yaml"

    rate_limit_per_minute: int = 10
    event_dedup_ttl_seconds: int = 300
    operation_timeout_seconds: int = 900

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
