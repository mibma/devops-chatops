import asyncio
import logging

import httpx

log = logging.getLogger(__name__)


class PrometheusQuerier:
    """Thin async wrapper around the Prometheus HTTP query API."""

    def __init__(self, base_url: str = "http://prometheus:9090"):
        self.base_url = base_url.rstrip("/")

    async def _query(self, promql: str) -> float | None:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(
                    f"{self.base_url}/api/v1/query",
                    params={"query": promql},
                )
                resp.raise_for_status()
                results = resp.json().get("data", {}).get("result", [])
                if not results:
                    return None
                return sum(float(r["value"][1]) for r in results)
        except Exception as exc:  # noqa: BLE001
            log.debug("prometheus query failed (%s): %s", promql, exc)
            return None

    async def get_ops_snapshot(self) -> dict:
        """Run all monitoring queries concurrently and return a labelled dict."""
        in_flight, cmd_rate, p95, err_rate, denial_rate, jenkins_q = await asyncio.gather(
            self._query("sum(chatops_operations_in_flight)"),
            self._query("sum(rate(chatops_commands_total[5m])) * 60"),
            self._query(
                "histogram_quantile(0.95,"
                " sum(rate(chatops_operation_duration_seconds_bucket[1h])) by (le))"
            ),
            self._query('sum(rate(chatops_commands_total{outcome="FAILED"}[1h])) * 60'),
            self._query("sum(rate(chatops_permission_denials_total[1h])) * 60"),
            self._query("chatops_jenkins_queue_depth"),
        )
        return {
            "in_flight":      in_flight,
            "cmd_per_min":    cmd_rate,
            "p95_seconds":    p95,
            "errors_per_min": err_rate,
            "denials_per_min": denial_rate,
            "jenkins_queue":  jenkins_q,
        }
