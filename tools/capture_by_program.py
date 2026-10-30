"""Attribute each decoded pump event to the program that actually emitted it.

Solana logs interleave "Program <id> invoke [n]" lines with "Program data: <b64>"
lines. The data line belongs to the most recently invoked program. Anchor event
discriminators are derived from the event NAME only, so two different programs
can both emit an event called "TradeEvent" and produce identical 8-byte
discriminators while having completely different field layouts.

This tool measures how much traffic comes from which program and whether our
decode makes sense per program.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import websockets


def walk_logs(logs: list[str]):
    """Yield (program_id, data_b64) pairs in log order."""
    current = None
    for line in logs:
        if line.startswith("Program "):
            parts = line.split()
            if len(parts) >= 3 and parts[2] == "invoke":
                current = parts[1]
        elif line.startswith("Program data: "):
            yield current, line[14:]


async def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/prog_capture.jsonl")
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
                        for prog, b64 in walk_logs(val.get("logs") or []):
                            f.write(json.dumps({"prog": prog, "data": b64,
                                                "slot": msg["params"]["result"]["context"]["slot"],
                                                "sig": val.get("signature", "")}) + "\n")
                            n += 1
            except Exception as e:  # noqa: BLE001
                print(f"[reconnect] {type(e).__name__}: {str(e)[:80]}", flush=True)
                await asyncio.sleep(1)
    print(f"captured {n} program-data lines over {time.time()-t0:.0f}s -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
