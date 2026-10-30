"""Verify decoded pump.fun events against on-chain bonding-curve accounts.

Layout-agnostic: we search the raw account bytes for the exact u64 values we
decoded. If our values appear in the account, the decode is right.
"""
from __future__ import annotations

import base64
import json
import struct
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pumpdesk.curve import is_standard
from pumpdesk.decoder import decode_program_data

RPC = "https://api.mainnet-beta.solana.com"


def rpc(method, params, url=RPC):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())


def load(path):
    creates, trades = {}, defaultdict(list)
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        ev = decode_program_data(o["data"])
        if not ev:
            continue
        if ev["kind"] == "create":
            creates[ev["mint"]] = ev
        elif ev["kind"] == "trade":
            trades[ev["mint"]].append(ev)
    return creates, trades


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/capture.jsonl"
    creates, trades = load(path)
    mints = [m for m in trades if m in creates]
    print(f"tokens with create: {len(mints)}")

    # batch fetch bonding curve accounts
    accts = {}
    for i in range(0, len(mints), 50):
        chunk = mints[i:i + 50]
        res = rpc("getMultipleAccounts", [[creates[m]["bonding_curve"] for m in chunk],
                                          {"encoding": "base64"}])
        for m, a in zip(chunk, res["result"]["value"]):
            if a:
                accts[m] = base64.b64decode(a["data"][0])
    print(f"bonding accounts found: {len(accts)}/{len(mints)}")

    fields = ("vsol", "vtok", "rsol", "rtok")
    stats = defaultdict(int)
    rows = []
    for m, raw in accts.items():
        evs = trades[m]
        last = evs[-1]
        hits = {}
        for f in fields:
            needle = struct.pack("<Q", last[f])
            hits[f] = [i for i in range(len(raw)) if raw[i:i + 8] == needle]
        found = [f for f in fields if hits[f]]
        std = is_standard(last["vsol"], last["vtok"], last["rtok"])
        stats[(std, tuple(sorted(found)))] += 1
        rows.append((m, std, hits, last, len(raw)))

    print("\n(standard, fields_found_in_account) -> count")
    for k, v in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"  std={k[0]!s:5} found={k[1]} -> {v}")

    print("\nper-token detail (first 8):")
    for m, std, hits, last, ln in rows[:8]:
        print(f"  mint={m[:12]} acct_len={ln} std={std}")
        for f in fields:
            print(f"     {f}={last[f]:>22} offsets={hits[f]}")

    # how many tokens have ALL four fields present (decode fully verified)
    allf = sum(1 for _, _, hits, _, _ in rows if all(hits[f] for f in fields))
    anyf = sum(1 for _, _, hits, _, _ in rows if any(hits[f] for f in fields))
    print(f"\nfully verified (all 4 u64 found in account): {allf}/{len(rows)}")
    print(f"partially verified (>=1 found): {anyf}/{len(rows)}")

    # constant-product check per token
    cp_std, cp_non = [], []
    for m, evs in trades.items():
        prods = [e["vsol"] * e["vtok"] for e in evs if e["vsol"] and e["vtok"]]
        if len(prods) < 3:
            continue
        rel = max(abs(p / prods[0] - 1) for p in prods)
        (cp_std if is_standard(evs[0]["vsol"], evs[0]["vtok"], evs[0]["rtok"]) else cp_non).append(rel)
    for name, arr in (("standard", cp_std), ("nonstandard", cp_non)):
        if arr:
            arr.sort()
            print(f"constant-product drift {name}: median={arr[len(arr)//2]:.2e} "
                  f"p90={arr[int(len(arr)*0.9)]:.2e} max={arr[-1]:.2e} (n={len(arr)})")


if __name__ == "__main__":
    main()
