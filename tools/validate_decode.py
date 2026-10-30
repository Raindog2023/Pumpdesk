"""Validate the TradeEvent decode against fresh on-chain state, and measure
event-layout variance.

Two questions:
1. Do all pump.fun TradeEvent blobs share one layout? (raw length variance,
   and whether our fixed offsets always land on the same fields)
2. Does our decode match the bonding curve account for the newest event we saw
   for each mint? (ground truth, fetched fresh)
"""
from __future__ import annotations

import base64
import collections
import json
import struct
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pumpdesk.curve import INITIAL_VSOL, K_STANDARD, VTOK_OFFSET
from pumpdesk.decoder import decode_program_data

RPC = "https://api.mainnet-beta.solana.com"


def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(RPC, data=body, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=40).read())


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/live_capture.jsonl")
    creates, trades = {}, collections.defaultdict(list)
    lens = collections.Counter()
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        try:
            raw = base64.b64decode(o["data"], validate=True)
        except Exception:  # noqa: BLE001, S112
            continue
        ev = decode_program_data(o["data"])
            continue
        if ev["kind"] == "create":
            creates[ev["mint"]] = ev
            lens[("create", len(raw))] += 1
        elif ev["kind"] == "trade":
            trades[ev["mint"]].append((o, ev))
            lens[("trade", len(raw))] += 1

    print("event blob lengths by kind:")
    for (kind, ln), n in sorted(lens.items()):
        print(f"  {kind:7} len={ln:>4} x{n}")

    # ---- ground truth: newest event per mint vs bonding curve account
    mints = list(trades)
    print(f"\nmints with trades: {len(mints)}")

    # bonding curve addresses come from create events
    bc = {m: creates[m]["bonding_curve"] for m in mints if m in creates}
    print(f"mints with a create event: {len(bc)}")

    accounts = {}
    keys = list(bc.items())
    for i in range(0, len(keys), 50):
        chunk = keys[i:i + 50]
        res = rpc("getMultipleAccounts", [[b for _, b in chunk], {"encoding": "base64"}])
        for (m, _b), a in zip(chunk, res["result"]["value"]):
            if a:
                accounts[m] = base64.b64decode(a["data"][0])
    print(f"bonding accounts fetched: {len(accounts)}")

    # locate our decoded values in the account
    exact = 0
    partial = 0
    missing = []
    for m, raw in accounts.items():
        newest = trades[m][-1][1]
        hits = 0
        for f in ("vtok", "vsol", "rsol", "rtok"):
            needle = struct.pack("<Q", newest[f])
            if any(raw[i:i + 8] == needle for i in range(len(raw))):
                hits += 1
        if hits == 4:
            exact += 1
        elif hits:
            partial += 1
        else:
            missing.append((m, newest))
    print("\nnewest event vs on-chain account:")
    print(f"  all 4 values found (exact match): {exact}/{len(accounts)}")
    print(f"  some values found:                 {partial}/{len(accounts)}")
    print(f"  no values found:                   {len(missing)}/{len(accounts)}")
    for m, e in missing[:4]:
        print(f"    {m[:14]} newest: v={e['vsol']} t={e['vtok']} rs={e['rsol']} rt={e['rtok']}")

    # ---- invariants per event, grouped by whether curve is "standard shaped"
    print("\nper-event invariants:")
    stats = collections.Counter()
    for m, rows in trades.items():
        for _o, e in rows:
            v, t, rs, rt = e["vsol"], e["vtok"], e["rsol"], e["rtok"]
            stats["tok_offset_exact"] += abs(t - rt - VTOK_OFFSET) < 10**11
            stats["sol_offset_30"] += abs(v - rs - INITIAL_VSOL) < 10**9
            stats["product_std"] += bool(v and t) and abs(v * t / K_STANDARD - 1) < 0.01
            stats["total"] += 1
    t = stats["total"]
    print(f"  total trade events: {t}")
    for k in ("tok_offset_exact", "sol_offset_30", "product_std"):
        print(f"  {k:18} {stats[k]:>6} ({stats[k]/t:6.1%})")


if __name__ == "__main__":
    main()
