"""Live pump.fun event stream over a Solana RPC websocket (logsSubscribe).

Auto-reconnects with backoff, dedupes repeats, and reconnects if the stream goes
silent (pump.fun trades many times per second, so silence means a dead socket).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import OrderedDict

import websockets

from .decoder import PUMP_PROGRAM, decode_program_data

log = logging.getLogger("pumpdesk.feed")
PLACEHOLDER_SIG = "1" * 64
PUBLIC_WSS = "wss://api.mainnet-beta.solana.com"


def events_from_notification(msg: dict) -> list:
    if msg.get("method") != "logsNotification":
        return []
    res = msg["params"]["result"]
    val = res["value"]
    if val.get("err"):
        return []
    slot, sig, out = res["context"]["slot"], val.get("signature", ""), []
    for line in val.get("logs") or []:
        if line.startswith("Program data: "):
            ev = decode_program_data(line[14:])
            if ev:
                # pump.fun logs each event twice per tx; payload includes reserves so
                # (signature, payload) is unique per real event
                out.append((slot, (sig, line[14:]), ev))
    return out


class Deduper:
    def __init__(self, maxlen: int = 50_000):
        self.seen: OrderedDict = OrderedDict()
        self.maxlen = maxlen

    def fresh(self, key) -> bool:
        if key in self.seen:
            return False
        self.seen[key] = None
        if len(self.seen) > self.maxlen:
            self.seen.popitem(last=False)
        return True


class Feed:
    def __init__(self, wss_url: str, on_event, silence_s: float = 20.0):
        self.url = wss_url or PUBLIC_WSS
        self.on_event = on_event
        self.silence_s = silence_s
        self.dedupe = Deduper()
        self.connected = False
        self.reconnects = 0
        self.last_msg = 0.0
        self.last_error = ""

    async def run(self):
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(self.url, max_size=None, ping_interval=15,
                                              ping_timeout=20, open_timeout=15) as ws:
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                                              "params": [{"mentions": [PUMP_PROGRAM]},
                                                         {"commitment": "processed"}]}))
                    self.connected, backoff = True, 1.0
                    log.info("feed connected: %s", self.url.split("?")[0])
                    while True:
                        raw = await asyncio.wait_for(ws.recv(), timeout=self.silence_s)
                        self.last_msg = time.time()
                        msg = json.loads(raw)
                        if "error" in msg:
                            raise RuntimeError(f"RPC error: {msg['error']}")
                        for slot, key, ev in events_from_notification(msg):
                            if self.dedupe.fresh(key):
                                self.on_event(slot, ev)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - any failure -> reconnect
                self.last_error = f"{type(e).__name__}: {e}"[:200]
                log.warning("feed dropped (%s); reconnecting in %.0fs", self.last_error, backoff)
            self.connected = False
            self.reconnects += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)
