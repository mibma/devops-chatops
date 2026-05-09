import logging
from collections import defaultdict
from uuid import UUID

from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.errors import SlackApiError

from ..adapters.ec2_adapter import EC2HealthReport
from ..models.intents import CommandIntent
from ..models.results import DeploymentStatus, RolloutStatus

log = logging.getLogger(__name__)


class SlackNotifier:
    """Posts messages and Block Kit cards back to Slack channels."""

    def __init__(self, bot_token: str):
        self.client = AsyncWebClient(token=bot_token) if bot_token else None

    async def post_text(self, channel: str, text: str) -> None:
        if not self.client or not channel:
            log.info("slack.post_text (noop): %s", text)
            return
        try:
            await self.client.chat_postMessage(channel=channel, text=text)
        except SlackApiError as exc:
            log.warning("slack post failed: %s", exc)

    async def post_approval_card(
        self,
        channel: str,
        tracking_id: UUID,
        intent: CommandIntent,
    ) -> None:
        tid = str(tracking_id)
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f":warning: Approval required: {intent.action.value}"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Service:*\n{intent.service or '-'}"},
                    {"type": "mrkdwn", "text": f"*Environment:*\n{intent.environment or '-'}"},
                    {"type": "mrkdwn", "text": f"*Version:*\n{intent.version or '-'}"},
                    {"type": "mrkdwn", "text": f"*Requested by:*\n<@{intent.requester_id}>"},
                ],
            },
            {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Tracking ID: `{tid}`"}]},
            {
                "type": "actions",
                "block_id": f"approval_{tid}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "action_id": f"approve_{tid}",
                        "value": tid,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "style": "danger",
                        "action_id": f"reject_{tid}",
                        "value": tid,
                    },
                ],
            },
        ]
        if not self.client:
            log.info("slack.post_approval_card (noop) tid=%s", tid)
            return
        try:
            await self.client.chat_postMessage(channel=channel, text="Approval required", blocks=blocks)
        except SlackApiError as exc:
            log.warning("slack approval post failed: %s", exc)

    async def post_deployment_card(self, channel: str, rollout: RolloutStatus) -> None:
        text = (
            f":rocket: Rolling update initiated for `{rollout.deployment}` "
            f"in `{rollout.namespace}` → image `{rollout.new_image}` (by <@{rollout.triggered_by}>)"
        )
        await self.post_text(channel, text)

    async def post_status_card(self, channel: str, status: DeploymentStatus) -> None:
        text = (
            f":bar_chart: *{status.name}* in `{status.namespace}`\n"
            f"Image: `{status.current_image}`\n"
            f"Replicas: {status.ready_replicas}/{status.desired_replicas} ready "
            f"({status.available_replicas} available)"
        )
        await self.post_text(channel, text)

    async def post_logs(self, channel: str, pod_name: str, logs: str) -> None:
        snippet = logs[-2800:] if len(logs) > 2800 else logs
        await self.post_text(channel, f":scroll: Logs for `{pod_name}`:\n```\n{snippet}\n```")

    async def post_ops_card(self, channel: str, snap: dict) -> None:
        def _fmt(v: float | None, suffix: str = "", decimals: int = 1) -> str:
            return "n/a" if v is None else f"{v:.{decimals}f}{suffix}"

        lines = [":zap: *Real-time Ops Pulse* (Prometheus — last 5m/1h)"]
        lines.append(f"• In-flight operations right now: *{_fmt(snap.get('in_flight'), decimals=0)}*")
        lines.append(f"• Commands/min (last 5 min): *{_fmt(snap.get('cmd_per_min'), '/min')}*")
        lines.append(f"• p95 operation time (last 1h): *{_fmt(snap.get('p95_seconds'), 's')}*")
        lines.append(f"• Failed ops rate (last 1h): *{_fmt(snap.get('errors_per_min'), '/min')}*")
        lines.append(f"• Permission denials (last 1h): *{_fmt(snap.get('denials_per_min'), '/min')}*")
        await self.post_text(channel, "\n".join(lines))

    async def post_metrics_card(self, channel: str, stats: dict) -> None:
        hours = stats.get("hours", 24)
        rows = stats.get("rows", [])
        unique_users = stats.get("unique_users", 0)

        if not rows:
            await self.post_text(channel, f":bar_chart: No commands recorded in the last {hours}h.")
            return

        by_action: dict = defaultdict(dict)
        for r in rows:
            by_action[r["action"]][r["outcome"]] = r

        lines = [f":bar_chart: *Deployment Dashboard — Last {hours}h*"]
        total = sum(r["count"] for r in rows)
        lines.append(f"• Total commands: *{total}*  |  Unique users: *{unique_users}*")

        for action, outcomes in sorted(by_action.items()):
            executed = outcomes.get("EXECUTED", {}).get("count", 0)
            failed   = outcomes.get("FAILED",   {}).get("count", 0)
            denied   = outcomes.get("DENIED",   {}).get("count", 0)
            total_a  = executed + failed + denied
            rate     = f"{executed / total_a * 100:.0f}%" if total_a else "n/a"

            avg_ms = outcomes.get("EXECUTED", {}).get("avg_ms")
            min_ms = outcomes.get("EXECUTED", {}).get("min_ms")
            max_ms = outcomes.get("EXECUTED", {}).get("max_ms")
            timing = ""
            if avg_ms is not None:
                timing = (
                    f" | avg {avg_ms/1000:.0f}s"
                    f" (min {(min_ms or 0)/1000:.0f}s"
                    f" / max {(max_ms or 0)/1000:.0f}s)"
                )

            parts = []
            if executed:
                parts.append(f":white_check_mark: {executed} ok")
            if failed:
                parts.append(f":x: {failed} failed")
            if denied:
                parts.append(f":no_entry: {denied} denied")

            lines.append(
                f"• *{action}*: {' / '.join(parts)} — *{rate} success*{timing}"
            )

        await self.post_text(channel, "\n".join(lines))

    async def post_incidents_card(self, channel: str, incidents: list[dict]) -> None:
        if not incidents:
            await self.post_text(channel, ":white_check_mark: No recent failures — all clear.")
            return

        lines = [f":rotating_light: *Recent Incidents (last {len(incidents)} failures)*"]
        for i, inc in enumerate(incidents, 1):
            ts = inc["timestamp"].strftime("%Y-%m-%d %H:%M") if inc.get("timestamp") else "unknown"
            action  = inc.get("action", "?")
            service = inc.get("service") or ""
            user_id = inc.get("user_id", "?")
            detail  = (inc.get("outcome_detail") or "no detail")[:100]
            svc     = f" `{service}`" if service else ""
            lines.append(f"{i}. [{ts}] *{action}*{svc} by <@{user_id}> — _{detail}_")

        await self.post_text(channel, "\n".join(lines))

    async def post_ec2_status_card(self, channel: str, report: EC2HealthReport) -> None:
        emoji = ":large_green_circle:" if report.healthy else ":red_circle:"
        lines = [f"{emoji} *Status: {report.target}*"]

        if report.instance_id:
            lines.append(
                f"• EC2 `{report.instance_id}` — state: *{report.instance_state or 'n/a'}*, "
                f"instance-check: *{report.instance_status or 'n/a'}*, "
                f"system-check: *{report.system_status or 'n/a'}*"
            )
        if report.public_dns or report.public_ip:
            lines.append(
                f"• Address: `{report.public_dns or report.public_ip}`"
            )
        if report.http_url:
            if report.http_status is not None:
                lines.append(
                    f"• HTTP `{report.http_url}` → *{report.http_status}* "
                    f"in {report.http_latency_ms} ms"
                )
            else:
                lines.append(
                    f"• HTTP `{report.http_url}` → unreachable "
                    f"({report.http_error or 'no response'})"
                )
        if report.ssh_checks:
            ssh_lines = "\n".join(f"  - {k}: `{v}`" for k, v in report.ssh_checks.items())
            lines.append(f"• Server checks:\n{ssh_lines}")
        if report.errors:
            lines.append(":warning: " + "; ".join(report.errors))

        await self.post_text(channel, "\n".join(lines))
