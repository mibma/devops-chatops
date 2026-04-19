from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from ..models.intents import Action, CommandIntent


@dataclass
class PermissionDecision:
    allowed: bool
    requires_approval: bool = False
    reason: Optional[str] = None


class PermissionChecker:
    """Loads a YAML permission matrix and evaluates command intents against it."""

    def __init__(self, permissions_file: str):
        self._path = Path(permissions_file)
        self._matrix: dict = {}
        self.reload()

    def reload(self) -> None:
        if not self._path.exists():
            self._matrix = {"permissions": {}, "approval_channels": {}, "approval_timeout_minutes": 15}
            return
        with self._path.open("r", encoding="utf-8") as f:
            self._matrix = yaml.safe_load(f) or {}

    def evaluate(self, intent: CommandIntent, roles: list[str]) -> PermissionDecision:
        if not roles:
            return PermissionDecision(False, reason="User has no assigned roles")

        perms = self._matrix.get("permissions", {})
        action_str = intent.action.value
        env = intent.environment or "development"

        any_allowed = False
        requires_approval = False

        for role in roles:
            role_cfg = perms.get(role)
            if not role_cfg:
                continue

            allowed_actions = role_cfg.get("allowed_actions", [])
            allowed_envs = role_cfg.get("allowed_environments", [])

            action_ok = "*" in allowed_actions or action_str in allowed_actions
            env_ok = "*" in allowed_envs or env in allowed_envs

            if action_ok and env_ok:
                any_allowed = True
                for rule in role_cfg.get("requires_approval", []) or []:
                    if rule.get("action") == action_str and rule.get("environment") == env:
                        requires_approval = True

        if not any_allowed:
            return PermissionDecision(
                False,
                reason=f"Role(s) {roles} cannot perform {action_str} on {env}",
            )

        return PermissionDecision(True, requires_approval=requires_approval)

    def approval_channel_for(self, action: Action, environment: Optional[str]) -> Optional[str]:
        channels = self._matrix.get("approval_channels", {})
        if action == Action.ROLLBACK:
            return channels.get("rollbacks")
        if environment == "production":
            return channels.get("production_deployments")
        return None
