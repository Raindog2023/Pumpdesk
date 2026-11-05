"""Solana WebSocket feed for real-time pump.fun program logs."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Callable

import websockets
from .decoder import events_from_notification

log = logging.getLogger("pumpdesk.feed")

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUBLIC_WSS = "wss://api.mainnet-beta.solana.com"


class Deduper:
    """Bounded LRU deduplication cache for transaction signatures/event keys."""
    def __init__(self, maxsize: int = 50_000):
        self.maxsize = maxsize
        self._seen: set[str] = set()
        self._order: deque[str] = deque()

    def fresh(self, key: str) -> bool:
        if not key:
            return True
        if key in self._seen:
            return False
        self._seen.add(key)
        self._order.append(key)
        if len(self._order) > self.maxsize:
            evicted = self._order.popleft()
            self._seen.discard(evicted)
        return True


class Feed:
    def __init__(self, wss_url: str | None, on_event: Callable[[int, dict], None], silence_s: float = 45.0):
        self.url = wss_url or PUBLIC_WSS
        self.on_event = on_event
        self.silence_s = silence_s
        self.dedupe = Deduper(maxsize=50_000)
        self.connected = False
        self.reconnects = 0
        self.last_msg = 0.0
        self.last_error = ""
        self._sub_id: int | None = None
        self._queue: asyncio.Queue[tuple[int, dict]] = asyncio.Queue(maxsize=10_000)

    async def _worker(self):
        """Worker task processing events independently from WebSocket ingestion."""
        while True:
            try:
                slot, ev = await self._queue.get()
                self.on_event(slot, ev)
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Error in feed on_event handler")

    async def run(self):
        # Import lazily to avoid circular dependencies
        from .decoder import events_from_notification

        worker_task = asyncio.create_task(self._worker())
        backoff = 1.0

        try:
            while True:
                try:
                    async with websockets.connect(
                        self.url,
                        max_size=None,
                        ping_interval=15,
                        ping_timeout=20,
                        open_timeout=15,
                    ) as ws:
                        # Send logsSubscribe for pump.fun program
                        sub_req = {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "logsSubscribe",
                            "params": [
                                {"mentions": [PUMP_PROGRAM]},
                                {"commitment": "processed"}
                            ]
                        }
                        await ws.send(json.dumps(sub_req))
                        self.connected = True
                        backoff = 1.0
                        log.info("Feed connected: %s", self.url.split("?")[0])

                        while True:
                            raw = await asyncio.wait_for(ws.recv(), timeout=self.silence_s)
                            self.last_msg = time.time()

                            try:
                                msg = json.loads(raw)
                            except json.JSONDecodeError:
                                log.warning("Received invalid non-JSON payload from WebSocket")
                                continue

                            # Subscription confirmation response
                            if "result" in msg and msg.get("id") == 1:
                                self._sub_id = msg["result"]
                                log.info("logsSubscribe confirmed (sub_id=%s)", self._sub_id)
                                continue

                            if "error" in msg:
                                raise RuntimeError(f"RPC error response: {msg['error']}")

                            # Process notification logs
                            for slot, key, ev in events_from_notification(msg):
                                if self.dedupe.fresh(key):
                                    if self._queue.full():
                                        # Drop oldest if queue is saturated to preserve real-time latency
                                        try:
                                            self._queue.get_nowait()
                                        except asyncio.QueueEmpty:
                                            pass
                                    self._queue.put_nowait((slot, ev))

                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.last_error = f"{type(e).__name__}: {str(e)[:200]}"
                    log.warning("Feed dropped (%s); reconnecting in %.0fs", self.last_error, backoff)
                    self.connected = False
                    self.reconnects += 1
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 30.0)
        finally:
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
