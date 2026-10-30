"""Record live pump.fun events to a JSONL file for offline verification.

Usage: python tools/capture.py <out.jsonl> <seconds> [wss_url]
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import websockets

from pumpdesk.decoder import PUMP_PROGRAM, decode_program_data


async def main():
    out = Path(sys.argv[1])
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 60
    url = sys.argv[3] if len(sys.argv) > 3 else "wss://api.mainnet-beta.solana.com"
    n = 0
    t0 = time.time()
    with out.open("w", encoding="utf-8") as f:  # noqa: ASYNC230
        async with websockets.connect(url, max_size=None, ping_interval=15, ping_timeout=20) as ws:
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                                      "params": [{"mentions": [PUMP_PROGRAM]}, {"commitment": "processed"}]}))
            while time.time() - t0 < secs:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=secs)
                except TimeoutError:
                    break
                msg = json.loads(raw)
                if msg.get("method") != "logsNotification":
                    continue
                val = msg["params"]["result"]["value"]
                if val.get("err"):
                    continue
                slot = msg["params"]["result"]["context"]["slot"]
                for line in val.get("logs") or []:
                    if line.startswith("Program data: "):
                        ev = decode_program_data(line[14:])
                        if ev:
                            f.write(json.dumps({"slot": slot, "sig": val.get("signature", ""),
                                                "kind": ev["kind"], "data": line[14:], "rx": time.time()}) + "\n")
                            n += 1
    print(f"captured {n} events over {time.time()-t0:.0f}s -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
