from typing import Optional

import redis.asyncio as aioredis


class RedisClient:
    def __init__(self, url: str):
        self._url = url
        self._client: Optional[aioredis.Redis] = None

    async def connect(self) -> None:
        self._client = aioredis.from_url(self._url, decode_responses=True)

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def client(self) -> aioredis.Redis:
        assert self._client is not None, "Redis not initialized"
        return self._client

    async def is_duplicate_event(self, event_id: str, ttl: int) -> bool:
        """SET NX with TTL — returns True if the event was already seen. Pass-through when Redis is down."""
        if not self.available:
            return False
        was_set = await self.client.set(f"dedup:{event_id}", "1", nx=True, ex=ttl)
        return not was_set

    async def hit_rate_limit(self, user_id: str, max_per_minute: int) -> bool:
        """Sliding minute window using INCR. Returns False (no limit) when Redis is down."""
        if not self.available:
            return False
        key = f"ratelimit:{user_id}"
        count = await self.client.incr(key)
        if count == 1:
            await self.client.expire(key, 60)
        return count > max_per_minute

    async def set_operation_state(self, tracking_id: str, field: str, value: str) -> None:
        if not self.available:
            return
        await self.client.hset(f"op:{tracking_id}", field, value)
        await self.client.expire(f"op:{tracking_id}", 3600)

    async def get_operation_state(self, tracking_id: str) -> dict:
        if not self.available:
            return {}
        return await self.client.hgetall(f"op:{tracking_id}")
