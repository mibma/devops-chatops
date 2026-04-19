from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID


class Outcome(str, Enum):
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


@dataclass
class AuditEntry:
    tracking_id: UUID
    user_id: str
    channel_id: str
    action: str
    raw_command: str
    outcome: Outcome
    username: Optional[str] = None
    service: Optional[str] = None
    environment: Optional[str] = None
    version: Optional[str] = None
    parsed_intent: Optional[dict[str, Any]] = None
    outcome_detail: Optional[str] = None
    duration_ms: Optional[int] = None
    approved_by: Optional[str] = None
    timestamp: datetime = None  # type: ignore
