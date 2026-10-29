"""Dump raw Solana log lines, attributed to the emitting program.

Answers: which programs emit Anchor events whose 8-byte discriminator collides
with pump.fun's "TradeEvent" discriminator (sha256("event:TradeEvent")[:8])?
"""
from __future__ import annotations

import asyncio
import collections
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pumpdesk.decoder import TRADE, decode_program_data  # noqa: E402

import websockets  # noqa: E402


async def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/raw_logs.jsonl")
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 60
    url = sys.argv[3] if len(sys.argv) > 3 else "wss://api.mainnet-beta.solana.com"
    n = 0
    t0 = time.time()
    with out.open("w", encoding="utf-8") as f:
        while time.time() - t0 < secs:
            try:
                async with websockets.connect(url, max_size=None, ping_interval=15,
                                              ping_timeout=20, open_timeout=15) as ws:
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                                              "params": [{"mentions": ["6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"]},
                                                         {"commitment": "processed"}]}))
                    deadline = time.time() + secs
                    while time.time() < deadline:
                        raw = await asyncio.wait_for(ws.recv(), timeout=max(1, deadline - time.time()))
                        msg = json.loads(raw)
                        if msg.get("method") != "logsNotification":
                            continue
                        val = msg["params"]["result"]["value"]
                        if val.get("err"):
                            continue
                        logs = val.get("logs") or []
                        f.write(json.dumps({
                            "slot": msg["params"]["result"]["context"]["slot"],
                            "sig": val.get("signature", ""),
                            "logs": logs,
                        }) + "\n")
                        n += 1
            except Exception as e:  # noqa: BLE001
                print(f"[reconnect] {type(e).__name__}: {str(e)[:70]}", flush=True)
                await asyncio.sleep(1)
    print(f"captured {n} notifications -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
