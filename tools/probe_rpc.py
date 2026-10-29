"""Test candidate Solana websocket RPC endpoints for logsSubscribe stability.

Reports: connect time, uptime, notification count, and slot span covered.
A good endpoint stays connected and covers slots continuously.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

import websockets

PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

CANDIDATES = [
    ("public-beta", "wss://api.mainnet-beta.solana.com"),
    ("publicnode", "wss://solana-rpc.publicnode.com"),
    ("drpc", "wss://solana.drpc.org"),
    ("ankr", "wss://rpc.ankr.com/solana/ws"),
    ("llamalabs", "wss://solana.llamarpc.com"),
    ("blockeden", "wss://api.blockeden.xyz/solana/67nCBdZQSH9z3YqDDjdm"),
    ("helius-nokey", "wss://mainnet.helius-rpc.com/?api-key=devnet"),
]


async def probe(name: str, url: str, secs: float) -> dict:
    res = {"name": name, "url": url, "ok": False, "msgs": 0, "slots": [], "err": "", "conn_s": 0.0}
    t0 = time.time()
    try:
        async with websockets.connect(url, max_size=None, ping_interval=15, ping_timeout=20,
                                      open_timeout=15) as ws:
            res["conn_s"] = round(time.time() - t0, 2)
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                                      "params": [{"mentions": [PUMP]}, {"commitment": "processed"}]}))
            deadline = time.time() + secs
            while time.time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(1, deadline - time.time()))
                except asyncio.TimeoutError:
                    res["err"] = "timeout"
                    break
                msg = json.loads(raw)
                if msg.get("method") == "logsNotification":
                    res["msgs"] += 1
                    res["slots"].append(msg["params"]["result"]["context"]["slot"])
                elif "error" in msg:
                    res["err"] = f"rpc error: {str(msg['error'])[:80]}"
                    break
            res["ok"] = True
    except Exception as e:  # noqa: BLE001
        res["err"] = f"{type(e).__name__}: {str(e)[:110]}"
    res["uptime_s"] = round(time.time() - t0, 1)
    if res["slots"]:
        res["slot_span"] = max(res["slots"]) - min(res["slots"])
        uniq = len(set(res["slots"]))
        res["uniq_slots"] = uniq
        # slots seen should be roughly contiguous for a healthy stream
        s = sorted(set(res["slots"]))
        holes = sum(1 for a, b in zip(s, s[1:]) if b - a > 1)
        res["slot_holes"] = holes
    return res


async def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 45
    only = sys.argv[2] if len(sys.argv) > 2 else None
    results = []
    for name, url in CANDIDATES:
        if only and only != name:
            continue
        r = await probe(name, url, secs)
        results.append(r)
        print(f"{r['name']:12} ok={str(r['ok']):5} conn={r['conn_s']:>5}s up={r['uptime_s']:>6}s "
              f"msgs={r['msgs']:>7} slots={r.get('uniq_slots','-'):>6} span={r.get('slot_span','-'):>6} "
              f"holes={r.get('slot_holes','-'):>4} err={r['err'][:60]}", flush=True)
    print("\nJSON " + json.dumps(results))


if __name__ == "__main__":
    asyncio.run(main())
