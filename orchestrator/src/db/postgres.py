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
    def pool(self) -> asyncpg.Pool:
        assert self._pool is not None, "Postgres pool not initialized"
        return self._pool

    async def get_user_roles(self, slack_user_id: str) -> list[str]:
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
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE pending_operations
                   SET status = $2,
                       approved_by = COALESCE($3, approved_by),
                       completed_at = CASE WHEN $2 IN ('COMPLETE','FAILED','EXPIRED')
                                           THEN NOW() ELSE completed_at END
                 WHERE tracking_id = $1
                """,
                tracking_id, status, approved_by,
            )

    async def get_pending_operation(self, tracking_id: UUID) -> Optional[dict]:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM pending_operations WHERE tracking_id = $1",
                tracking_id,
            )
            return dict(row) if row else None
