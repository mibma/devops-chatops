import json
from typing import Optional
from uuid import UUID

import asyncpg

from ..models.audit import AuditEntry


class Postgres:
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=10)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    @property
    def available(self) -> bool:
        return self._pool is not None

    @property
    def pool(self) -> asyncpg.Pool:
        assert self._pool is not None, "Postgres pool not initialized"
        return self._pool

    async def get_user_roles(self, slack_user_id: str) -> list[str]:
        if not self.available:
            return []
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT roles FROM user_roles WHERE slack_user_id = $1",
                slack_user_id,
            )
            if not row:
                return []
            return list(row["roles"])

    async def upsert_user(self, slack_user_id: str, display_name: str, email: str, roles: list[str]) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO user_roles (slack_user_id, display_name, email, roles)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (slack_user_id) DO UPDATE
                  SET display_name = EXCLUDED.display_name,
                      email        = EXCLUDED.email,
                      roles        = EXCLUDED.roles,
                      updated_at   = NOW()
                """,
                slack_user_id, display_name, email, roles,
            )

    async def write_audit(self, entry: AuditEntry) -> None:
        if not self.available:
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_log (
                  tracking_id, user_id, username, channel_id,
                  action, service, environment, version,
                  raw_command, parsed_intent, outcome, outcome_detail,
                  duration_ms, approved_by
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                """,
                entry.tracking_id,
                entry.user_id,
                entry.username,
                entry.channel_id,
                entry.action,
                entry.service,
                entry.environment,
                entry.version,
                entry.raw_command,
                json.dumps(entry.parsed_intent) if entry.parsed_intent else None,
                entry.outcome.value,
                entry.outcome_detail,
                entry.duration_ms,
                entry.approved_by,
            )

    async def create_pending_operation(
        self,
        tracking_id: UUID,
        initiated_by: str,
        action: str,
        payload: dict,
    ) -> None:
        if not self.available:
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pending_operations (tracking_id, initiated_by, action, payload)
                VALUES ($1, $2, $3, $4)
                """,
                tracking_id, initiated_by, action, json.dumps(payload),
            )

    async def set_operation_status(
        self,
        tracking_id: UUID,
        status: str,
        approved_by: Optional[str] = None,
    ) -> None:
        if not self.available:
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE pending_operations
                   SET status = $2::varchar,
                       approved_by = COALESCE($3, approved_by),
                       completed_at = CASE WHEN $2::varchar IN ('COMPLETE','FAILED','EXPIRED')
                                           THEN NOW() ELSE completed_at END
                 WHERE tracking_id = $1
                """,
                tracking_id, status, approved_by,
            )

    async def get_pending_operation(self, tracking_id: UUID) -> Optional[dict]:
        if not self.available:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM pending_operations WHERE tracking_id = $1",
                tracking_id,
            )
            return dict(row) if row else None

    async def get_deployment_stats(self, hours: int = 24) -> dict:
        if not self.available:
            return {"hours": hours, "rows": [], "unique_users": 0}
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT action, outcome,
                       COUNT(*)::int                   AS count,
                       AVG(duration_ms)::int           AS avg_ms,
                       MIN(duration_ms)::int           AS min_ms,
                       MAX(duration_ms)::int           AS max_ms
                FROM audit_log
                WHERE timestamp > NOW() - make_interval(hours => $1)
                GROUP BY action, outcome
                ORDER BY action, outcome
                """,
                hours,
            )
            unique_users = await conn.fetchval(
                """
                SELECT COUNT(DISTINCT user_id)
                FROM audit_log
                WHERE timestamp > NOW() - make_interval(hours => $1)
                """,
                hours,
            )
        return {
            "hours": hours,
            "rows": [dict(r) for r in rows],
            "unique_users": int(unique_users or 0),
        }

    async def get_recent_incidents(self, limit: int = 10) -> list[dict]:
        if not self.available:
            return []
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT user_id, action, service, outcome_detail, timestamp
                FROM audit_log
                WHERE outcome = 'FAILED'
                ORDER BY timestamp DESC
                LIMIT $1
                """,
                limit,
            )
        return [dict(r) for r in rows]
