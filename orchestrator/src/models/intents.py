from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Action(str, Enum):
    DEPLOY = "DEPLOY"
    BUILD = "BUILD"
    STATUS = "STATUS"
    ROLLBACK = "ROLLBACK"
    LOGS = "LOGS"
    RESTART = "RESTART"
    SCALE = "SCALE"
    HELP = "HELP"


@dataclass
class CommandIntent:
    action: Action
    service: Optional[str] = None
    environment: Optional[str] = None
    version: Optional[str] = None
    namespace: Optional[str] = None
    replicas: Optional[int] = None
    requester_id: str = ""
    requester_roles: list[str] = field(default_factory=list)
    channel_id: str = ""
    raw_input: str = ""
