import asyncio
import logging
import time
from dataclasses import asdict
from datetime import datetime
from uuid import UUID, uuid4

from ..adapters.docker_adapter import DockerAdapter
from ..adapters.ec2_adapter import EC2Adapter
from ..adapters.jenkins_adapter import JenkinsAdapter
from ..adapters.kubernetes_adapter import KubernetesAdapter
from ..db.postgres import Postgres
from ..db.redis_client import RedisClient
from ..models.audit import AuditEntry, Outcome
from ..models.intents import Action, CommandIntent
from ..monitoring.metrics import (
    COMMANDS_TOTAL,
    OPERATIONS_IN_FLIGHT,
    OPERATION_DURATION,
    PERMISSION_DENIALS,
)
from ..notifications.slack_notifier import SlackNotifier
from .command_parser import CommandParser
from .permission_checker import PermissionChecker
from ..monitoring.prometheus_querier import PrometheusQuerier

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        parser: CommandParser,
        permissions: PermissionChecker,
        postgres: Postgres,
        redis_client: RedisClient,
        slack: SlackNotifier,
        jenkins: JenkinsAdapter | None,
        docker: DockerAdapter | None,
        k8s: KubernetesAdapter | None,
        ec2: EC2Adapter | None = None,
        prometheus: PrometheusQuerier | None = None,
    ):
        self.parser = parser
        self.permissions = permissions
        self.db = postgres
        self.redis = redis_client
        self.slack = slack
        self.jenkins = jenkins
        self.docker = docker
        self.k8s = k8s
        self.ec2 = ec2
        self.prometheus = prometheus

    async def execute(
        self,
        raw_text: str,
        action: Action,
        user_id: str,
        channel_id: str,
    ) -> dict:
        tracking_id = uuid4()
        started = time.perf_counter()

        roles = await self.db.get_user_roles(user_id)
        intent = self.parser.parse(raw_text, action_hint=action)
        intent.requester_id = user_id
        intent.requester_roles = roles
        intent.channel_id = channel_id

        decision = self.permissions.evaluate(intent, roles)

        if not decision.allowed:
            PERMISSION_DENIALS.labels(
                user_role=",".join(roles) or "none",
                action=intent.action.value,
                environment=intent.environment or "unknown",
            ).inc()
            await self._audit(tracking_id, intent, Outcome.DENIED, decision.reason)
            await self.slack.post_text(
                channel_id,
                f":no_entry: Denied — {decision.reason}",
            )
            COMMANDS_TOTAL.labels(intent.action.value, intent.environment or "unknown", "denied").inc()
            return {"tracking_id": str(tracking_id), "status": "DENIED"}

        if decision.requires_approval:
            await self.db.create_pending_operation(
                tracking_id, user_id, intent.action.value, asdict(intent),
            )
            approval_channel = self.permissions.approval_channel_for(intent.action, intent.environment)
            await self.slack.post_approval_card(
                approval_channel or channel_id, tracking_id, intent,
            )
            await self._audit(tracking_id, intent, Outcome.APPROVED, "pending approval")
            return {"tracking_id": str(tracking_id), "status": "PENDING_APPROVAL"}

        asyncio.create_task(self._run(tracking_id, intent, started))
        return {"tracking_id": str(tracking_id), "status": "EXECUTING"}

    async def approve(self, tracking_id: UUID, approved_by: str) -> dict:
        op = await self.db.get_pending_operation(tracking_id)
        if not op or op["status"] != "PENDING":
            return {"status": "NOT_FOUND_OR_ALREADY_HANDLED"}
        await self.db.set_operation_status(tracking_id, "APPROVED", approved_by)
        payload = op["payload"]
        if isinstance(payload, str):
            import json
            payload = json.loads(payload)
        intent = CommandIntent(
            action=Action(payload["action"]),
            service=payload.get("service"),
            environment=payload.get("environment"),
            version=payload.get("version"),
            namespace=payload.get("namespace"),
            replicas=payload.get("replicas"),
            requester_id=payload.get("requester_id", op["initiated_by"]),
            requester_roles=payload.get("requester_roles", []),
            channel_id=payload.get("channel_id", ""),
            raw_input=payload.get("raw_input", ""),
        )
        asyncio.create_task(self._run(tracking_id, intent, time.perf_counter()))
        return {"status": "EXECUTING"}

    async def reject(self, tracking_id: UUID, rejected_by: str) -> dict:
        await self.db.set_operation_status(tracking_id, "EXPIRED", rejected_by)
        return {"status": "REJECTED"}

    async def _run(self, tracking_id: UUID, intent: CommandIntent, started: float) -> None:
        OPERATIONS_IN_FLIGHT.labels(intent.action.value).inc()
        try:
            detail = await self._dispatch(intent, tracking_id)
            duration_ms = int((time.perf_counter() - started) * 1000)
            await self._audit(tracking_id, intent, Outcome.EXECUTED, detail, duration_ms)
            await self.db.set_operation_status(tracking_id, "COMPLETE")
            COMMANDS_TOTAL.labels(intent.action.value, intent.environment or "unknown", "success").inc()
            OPERATION_DURATION.labels(intent.action.value, intent.environment or "unknown").observe(
                time.perf_counter() - started,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("operation failed", extra={"tracking_id": str(tracking_id)})
            duration_ms = int((time.perf_counter() - started) * 1000)
            await self._audit(tracking_id, intent, Outcome.FAILED, str(exc), duration_ms)
            await self.db.set_operation_status(tracking_id, "FAILED")
            await self.slack.post_text(intent.channel_id, f":x: Operation failed: {exc}")
            COMMANDS_TOTAL.labels(intent.action.value, intent.environment or "unknown", "failed").inc()
        finally:
            OPERATIONS_IN_FLIGHT.labels(intent.action.value).dec()

    def _use_ec2(self, service: str | None) -> bool:
        if self.ec2 is None:
            return False
        static = {"ec2", "server", None, ""}
        return (
            service in static
            or service == self.ec2.target_name
            or self.k8s is None
        )

    async def _dispatch(self, intent: CommandIntent, tracking_id: UUID) -> str:
        action = intent.action
        if action == Action.BUILD:
            assert self.jenkins, "Jenkins adapter not configured"
            result = await self.jenkins.trigger_build(
                intent.service or "default",
                {"VERSION": intent.version or "latest"},
                intent.requester_id,
            )
            await self.slack.post_text(
                intent.channel_id,
                f":hammer_and_wrench: Build queued for `{intent.service}` — queue #{result.queue_item}",
            )
            return f"queued:{result.queue_item}"

        if action == Action.DEPLOY:
            use_ec2 = self._use_ec2(intent.service)
            if use_ec2:
                msg = await self.ec2.deploy(intent.version or "latest", intent.requester_id)
                await self.slack.post_text(intent.channel_id, msg)
                return f"ec2-deploy:{intent.version or 'latest'}"
            assert self.k8s, "Kubernetes adapter not configured"
            namespace = intent.namespace or intent.environment or "default"
            image = f"{intent.service}:{intent.version or 'latest'}"
            rollout = await self.k8s.trigger_rolling_update(
                intent.service or "unknown", namespace, image, intent.requester_id,
            )
            await self.slack.post_deployment_card(intent.channel_id, rollout)
            return rollout.status

        if action == Action.STATUS:
            use_ec2 = self._use_ec2(intent.service)
            if use_ec2:
                report = await self.ec2.get_health()
                await self.slack.post_ec2_status_card(intent.channel_id, report)
                return (
                    f"ec2:{report.instance_state or 'unknown'}"
                    f" http:{report.http_status or 'n/a'}"
                )
            assert self.k8s, "No status adapter configured (set EC2_HTTP_URL or KUBECONFIG)"
            namespace = intent.namespace or intent.environment or "default"
            status = await self.k8s.get_deployment_status(intent.service or "", namespace)
            await self.slack.post_status_card(intent.channel_id, status)
            return f"ready:{status.ready_replicas}/{status.desired_replicas}"

        if action == Action.ROLLBACK:
            use_ec2 = self._use_ec2(intent.service)
            if use_ec2:
                msg = await self.ec2.rollback(intent.requester_id, intent.version or "")
                await self.slack.post_text(intent.channel_id, msg)
                return msg
            assert self.k8s, "Kubernetes adapter not configured"
            namespace = intent.namespace or intent.environment or "default"
            msg = await self.k8s.rollback_deployment(
                intent.service or "", namespace, intent.requester_id,
            )
            await self.slack.post_text(intent.channel_id, f":rewind: {msg}")
            return msg

        if action == Action.LOGS:
            use_ec2 = self._use_ec2(intent.service)
            if use_ec2:
                svc = intent.service or self.ec2.service_name
                logs = await self.ec2.get_logs(svc)
                await self.slack.post_logs(intent.channel_id, svc, logs)
                return f"ec2-logs:{svc}"
            assert self.k8s, "Kubernetes adapter not configured"
            namespace = intent.namespace or intent.environment or "default"
            pods = await self.k8s.get_pod_list(namespace, f"app={intent.service}")
            if not pods:
                await self.slack.post_text(intent.channel_id, f":warning: No pods matched `app={intent.service}` in `{namespace}`")
                return "no-pods"
            logs = await self.k8s.get_pod_logs(pods[0].name, namespace, tail_lines=50)
            await self.slack.post_logs(intent.channel_id, pods[0].name, logs)
            return f"logs:{pods[0].name}"

        if action == Action.SCALE:
            raise NotImplementedError("SCALE action not wired yet")

        if action == Action.RESTART:
            raise NotImplementedError("RESTART action not wired yet")

        if action == Action.METRICS:
            stats = await self.db.get_deployment_stats(hours=24)
            await self.slack.post_metrics_card(intent.channel_id, stats)
            total = sum(r["count"] for r in stats.get("rows", []))
            return f"metrics:ok rows={total}"

        if action == Action.INCIDENTS:
            incidents = await self.db.get_recent_incidents(limit=10)
            await self.slack.post_incidents_card(intent.channel_id, incidents)
            return f"incidents:{len(incidents)}"

        if action == Action.CAPACITY:
            assert self.ec2, "EC2 adapter not configured (set EC2_SSH_HOST in .env)"
            report = await self.ec2.get_health()
            await self.slack.post_ec2_status_card(intent.channel_id, report)
            return f"capacity:ok ssh={bool(report.ssh_checks)}"

        if action == Action.OPS:
            assert self.prometheus, "Prometheus not configured (add prometheus service to docker-compose)"
            snap = await self.prometheus.get_ops_snapshot()
            await self.slack.post_ops_card(intent.channel_id, snap)
            return f"ops:ok in_flight={snap.get('in_flight')}"

        if action == Action.HELP:
            await self.slack.post_text(
                intent.channel_id,
                "Available commands: /deploy /build /status /rollback /logs /restart /scale",
            )
            return "help"

        raise ValueError(f"Unhandled action: {action}")

    async def _audit(
        self,
        tracking_id: UUID,
        intent: CommandIntent,
        outcome: Outcome,
        detail: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        entry = AuditEntry(
            tracking_id=tracking_id,
            user_id=intent.requester_id,
            channel_id=intent.channel_id,
            action=intent.action.value,
            raw_command=intent.raw_input,
            outcome=outcome,
            service=intent.service,
            environment=intent.environment,
            version=intent.version,
            parsed_intent=asdict(intent),
            outcome_detail=detail,
            duration_ms=duration_ms,
            timestamp=datetime.utcnow(),
        )
        try:
            await self.db.write_audit(entry)
        except Exception:  # noqa: BLE001
            log.exception("failed to write audit entry", extra={"tracking_id": str(tracking_id)})
