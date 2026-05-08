from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings

_PROJECT_ROOT_ENV = Path(__file__).resolve().parent.parent.parent / ".env"


class Settings(BaseSettings):
    database_url: str = "postgresql://chatops:password@localhost:5432/chatops"
    redis_url: str = "redis://localhost:6379/0"

    jenkins_url: str = "http://jenkins.your-company.com"
    jenkins_user: str = "chatops-bot"
    jenkins_token: str = ""

    slack_bot_token: str = ""

    kubeconfig: Optional[str] = None
    in_cluster: bool = False

    permissions_file: str = "/app/config/permissions.yaml"

    rate_limit_per_minute: int = 10
    event_dedup_ttl_seconds: int = 300
    operation_timeout_seconds: int = 900

    # ---- EC2 / hello-cicd target ----
    ec2_target_name: str = "hello-cicd"
    ec2_http_url: str = ""           # e.g. http://ec2-x-x-x-x.compute-1.amazonaws.com/
    ec2_instance_id: str = ""        # e.g. i-0123456789abcdef0
    aws_region: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    ec2_ssh_host: str = ""
    ec2_ssh_user: str = ""
    ec2_ssh_key_path: str = ""
    ec2_service_name: str = "nginx"
    ec2_app_dir: str = "~/app"

    class Config:
        env_file = str(_PROJECT_ROOT_ENV)
        case_sensitive = False
        extra = "ignore"


settings = Settings()
