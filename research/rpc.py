"""Async Triton JSON-RPC client with a global rate limiter and Triton's required 429 backoff."""
from __future__ import annotations

import asyncio
import os
import random
import time

import aiohttp
import orjson


def load_env(path: str = os.path.join(os.path.dirname(__file__), "..", ".env")) -> None:
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)


class RateLimiter:
    def __init__(self, rps: float):
        self.rps = rps
        self.tokens = rps
        self.last = time.monotonic()
        self.lock = asyncio.Lock()
        self.pause_until = 0.0

    async def acquire(self):
        while True:
            async with self.lock:
                now = time.monotonic()
                if now < self.pause_until:
                    wait = self.pause_until - now
                else:
                    self.tokens = min(self.rps, self.tokens + (now - self.last) * self.rps)
                    self.last = now
                    if self.tokens >= 1:
                        self.tokens -= 1
                        return
                    wait = (1 - self.tokens) / self.rps
            await asyncio.sleep(wait)

    def pause(self, secs: float):
        self.pause_until = max(self.pause_until, time.monotonic() + secs)


class Rpc:
    def __init__(self, url: str | None = None, rps: float = 80, concurrency: int = 48):
        load_env()
        self.url = url or os.environ["TRITON_RPC_URL"]
        self.rl = RateLimiter(rps)
        self.sem = asyncio.Semaphore(concurrency)
        self.session: aiohttp.ClientSession | None = None
        self.calls = 0
        self.bytes = 0
        self.errors = 0

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(total=120)
        self.session = aiohttp.ClientSession(timeout=timeout, trust_env=True,
                                             connector=aiohttp.TCPConnector(limit=64))
        return self

    async def __aexit__(self, *a):
        await self.session.close()

    async def call(self, method: str, params: list, retries: int = 8):
        body = orjson.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        for attempt in range(retries):
            await self.rl.acquire()
            async with self.sem:
                try:
                    async with self.session.post(self.url, data=body,
                                                 headers={"content-type": "application/json"}) as r:
                        raw = await r.read()
                        self.calls += 1
                        self.bytes += len(raw)
                        if r.status == 429:
                            self.rl.pause(10.5)
                            continue
                        if r.status >= 500:
                            await asyncio.sleep(1 + attempt + random.random())
                            continue
                        d = orjson.loads(raw)
                except (aiohttp.ClientError, asyncio.TimeoutError, orjson.JSONDecodeError):
                    self.errors += 1
                    await asyncio.sleep(1 + attempt + random.random())
                    continue
            if "error" in d:
                err = d["error"]
                code = err.get("code")
                if code in (-32005, -32429, 429) or "rate" in str(err.get("message", "")).lower():
                    self.rl.pause(10.5)
                    continue
                if code in (-32004, -32014, -32007, -32009):  # block/slot not available yet etc.
                    await asyncio.sleep(1 + attempt)
                    continue
                raise RuntimeError(f"{method} error: {err}")
            return d["result"]
        raise RuntimeError(f"{method}: retries exhausted")

    async def txs_for_address(self, address: str, *, full: bool = True, order: str = "asc", limit: int = 100,
                              slot_gte: int | None = None, slot_lt: int | None = None,
                              status: str = "any", max_pages: int = 10_000, token: str | None = None):
        """Async generator over getTransactionsForAddress results (Triton custom method)."""
        filters = {"status": status}
        if slot_gte is not None or slot_lt is not None:
            f = {}
            if slot_gte is not None:
                f["gte"] = slot_gte
            if slot_lt is not None:
                f["lt"] = slot_lt
            filters["slot"] = f
        opts = {"transactionDetails": "full" if full else "signatures", "sortOrder": order, "limit": limit,
                "filters": filters}
        if full:
            opts.update({"encoding": "json", "maxSupportedTransactionVersion": 1})
        pages = 0
        while pages < max_pages:
            if token:
                opts["paginationToken"] = token
            res = await self.call("getTransactionsForAddress", [address, opts])
            data = res.get("data", [])
            for t in data:
                yield t
            token = res.get("paginationToken")
            pages += 1
            if not token or len(data) < limit:
                break
