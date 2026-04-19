from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class BuildStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


@dataclass
class BuildResult:
    job_name: str
    queue_item: int | None
    status: BuildStatus
    triggered_by: str
    build_number: int | None = None


@dataclass
class ContainerInfo:
    id: str
    name: str
    image: str
    status: str
    ports: dict
    created: str
    labels: dict


@dataclass
class ImageInfo:
    id: str
    tags: list[str]
    size_mb: float
    created: str


@dataclass
class PodInfo:
    name: str
    namespace: str
    phase: str
    ready: bool
    restarts: int
    node: str | None
    age: str


@dataclass
class DeploymentStatus:
    name: str
    namespace: str
    desired_replicas: int
    ready_replicas: int
    available_replicas: int
    current_image: str
    conditions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RolloutStatus:
    deployment: str
    namespace: str
    new_image: str
    triggered_by: str
    status: str
