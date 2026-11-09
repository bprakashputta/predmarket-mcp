"""Shared HTTP plumbing: one pooled client per venue, retries with backoff, and a TTL cache
that collapses concurrent identical requests into a single upstream call."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

RETRY_STATUS = {429, 500, 502, 503, 504}


class TTLCache:
    """Async cache with per-entry expiry and in-flight de-duplication."""

    def __init__(self, max_entries: int = 2048) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._inflight: dict[str, asyncio.Future[Any]] = {}
        self._max = max_entries

    async def get_or_set(self, key: str, ttl: float, loader: Callable[[], Awaitable[Any]]) -> Any:
        now = time.monotonic()
        hit = self._data.get(key)
        if hit and hit[0] > now:
            return hit[1]
        if key in self._inflight:
            return await self._inflight[key]

        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._inflight[key] = fut
        try:
            value = await loader()
        except BaseException as exc:
            fut.set_exception(exc)
            fut.exception()  # mark retrieved so asyncio doesn't warn when nobody else awaits
            raise
        else:
            fut.set_result(value)
            if len(self._data) >= self._max:
                # drop the entry closest to expiry; O(n) only when full
                self._data.pop(min(self._data, key=lambda k: self._data[k][0]), None)
            self._data[key] = (now + ttl, value)
            return value
        finally:
            self._inflight.pop(key, None)


class HttpClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        retries: int = 4,
        max_rps: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"User-Agent": "predmarket-mcp/0.1"},
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            transport=transport,
        )
        self._retries = retries
        self._interval = 1.0 / max_rps
        self._next_slot = 0.0
        self._slot_lock = asyncio.Lock()

    async def _throttle(self) -> None:
        """Space requests evenly so bursts (e.g. paging a catalog) stay under the venue limit."""
        async with self._slot_lock:
            now = time.monotonic()
            wait = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + self._interval
        if wait > 0:
            await asyncio.sleep(wait)

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(self._retries + 1):
            await self._throttle()
            delay = min(0.5 * 2**attempt, 8.0) + random.uniform(0, 0.2)
            try:
                resp = await self._client.get(path, params=clean)
            except httpx.TransportError:
                if attempt == self._retries:
                    raise
            else:
                if resp.status_code not in RETRY_STATUS or attempt == self._retries:
                    resp.raise_for_status()
                    return resp.json()
                retry_after = resp.headers.get("Retry-After", "")
                if retry_after.replace(".", "", 1).isdigit():
                    delay = min(float(retry_after), 30.0)
            await asyncio.sleep(delay)
        raise RuntimeError("unreachable")

    async def aclose(self) -> None:
        await self._client.aclose()
