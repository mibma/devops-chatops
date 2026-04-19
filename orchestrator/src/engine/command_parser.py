import re
from typing import Optional

from ..models.intents import Action, CommandIntent


class CommandParser:
    """
    Two-stage parser: structured slash-command parsing first, then
    keyword-based free-form classification as a fallback.
    """

    PARAM_PATTERN = re.compile(r"(\w+)=(\S+)")
    ENV_KEYWORDS = {
        "prod", "production", "staging", "stage",
        "dev", "development", "qa",
    }

    def parse(self, text: str, action_hint: Optional[Action] = None) -> CommandIntent:
        text = (text or "").strip().lower()
        params = dict(self.PARAM_PATTERN.findall(text))

        if action_hint:
            return CommandIntent(
                action=action_hint,
                service=params.get("service") or params.get("svc") or self._extract_service(text),
                environment=self._normalize_env(params.get("env") or params.get("environment")),
                version=params.get("version") or params.get("ver") or params.get("tag"),
                namespace=params.get("namespace") or params.get("ns"),
                replicas=int(params["replicas"]) if "replicas" in params else None,
                raw_input=text,
            )

        return self._classify_free_form(text)

    def _normalize_env(self, env: Optional[str]) -> Optional[str]:
        if not env:
            return None
        env = env.lower()
        if env in ("prod", "production"):
            return "production"
        if env in ("stage", "staging"):
            return "staging"
        if env in ("dev", "development"):
            return "development"
        return env

    def _classify_free_form(self, text: str) -> CommandIntent:
        action = Action.HELP

        if any(w in text for w in ["deploy", "release", "ship", "push"]):
            action = Action.DEPLOY
        elif any(w in text for w in ["build", "compile", "run job"]):
            action = Action.BUILD
        elif any(w in text for w in ["status", "health", "check", "how is", "state"]):
            action = Action.STATUS
        elif any(w in text for w in ["rollback", "revert", "undo", "previous version"]):
            action = Action.ROLLBACK
        elif any(w in text for w in ["log", "logs", "output", "console"]):
            action = Action.LOGS
        elif any(w in text for w in ["restart", "bounce", "cycle"]):
            action = Action.RESTART
        elif any(w in text for w in ["scale", "replicas", "instances"]):
            action = Action.SCALE

        service = self._extract_service(text)
        environment = next((e for e in self.ENV_KEYWORDS if e in text.split()), None)

        return CommandIntent(
            action=action,
            service=service,
            environment=self._normalize_env(environment),
            raw_input=text,
        )

    def _extract_service(self, text: str) -> Optional[str]:
        stop_words = {
            "the", "a", "an", "deploy", "build", "status", "check",
            "rollback", "logs", "restart", "scale", "to", "for", "in",
            "on", "can", "you", "please", "hey", "bot", "service", "app",
        } | self.ENV_KEYWORDS

        words = text.split()
        candidates = [w for w in words if w not in stop_words and len(w) > 2 and "=" not in w]
        return candidates[0] if candidates else None
