"""Dump the raw field layout of trade events for one mint, grouped by blob size.

There are two trade-event blob sizes in the feed (390 vs 405 raw bytes). If
they are two different structs with the same discriminator, our fixed-offset
decode silently reads the wrong fields for one of them.
"""
from __future__ import annotations

import base64
import collections
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pumpdesk.decoder import decode_program_data  # noqa: E402

# offsets of interest in the classic layout, relative to the discriminator
LAYOUT = [
    (0, "mint", 32), (32, "sol_amount", 8), (40, "token_amount", 8),
    (48, "is_buy", 1), (49, "user", 32), (81, "timestamp", 8),
    (89, "virtual_sol_reserves", 8), (97, "virtual_token_reserves", 8),
    (105, "real_sol_reserves", 8), (113, "real_token_reserves", 8),
    (121, "fee_recipient", 32), (153, "fee_bps", 8), (161, "fee_amount", 8),
    (169, "creator", 32), (201, "creator_fee_bps", 8), (209, "creator_fee_amount", 8),
]


def dump(raw: bytes, label: str):
    print(f"\n--- {label}: {len(raw)} bytes ---")
    for off, name, size in LAYOUT:
        chunk = raw[8 + off: 8 + off + size]
        if len(chunk) < size:
            print(f"  +{off:<4} {name:24} <truncated>")
            continue
        if size == 32:
            print(f"  +{off:<4} {name:24} {chunk.hex()}")
        elif size == 8:
            print(f"  +{off:<4} {name:24} {struct.unpack('<Q', chunk)[0]}")
        else:
            print(f"  +{off:<4} {name:24} {chunk[0]}")


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/live_capture.jsonl")
    want = sys.argv[2] if len(sys.argv) > 2 else None
    by_mint = collections.defaultdict(dict)  # mint -> size -> (raw, slot)
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        raw = base64.b64decode(o["data"])
        ev = decode_program_data(o["data"])
        if not ev or ev["kind"] != "trade":
            continue
        if want and ev["mint"] != want:
            continue
        by_mint[ev["mint"]].setdefault(len(raw), (raw, o["slot"], ev))

    multi = {m: d for m, d in by_mint.items() if len(d) > 1}
    print(f"mints with trade events: {len(by_mint)}; with >1 blob size: {len(multi)}")
    sizes = collections.Counter()
    for m, d in by_mint.items():
        for s in d:
            sizes[s] += 1
    print("blob sizes:", dict(sorted(sizes.items())))

    target = want or next(iter(multi)) if multi else next(iter(by_mint))
    print(f"\n=== mint {target} ===")
    d = by_mint[target]
    for size, (raw, slot, ev) in sorted(d.items()):
        dump(raw, f"size={size} slot={slot}")
        print(f"  decoded: mint={ev['mint']} buy={ev['is_buy']} sol={ev['sol']} "
              f"tokens={ev['tokens']} vsol={ev['vsol']} vtok={ev['vtok']} "
              f"rsol={ev['rsol']} rtok={ev['rtok']} ts={ev['ts']}")


if __name__ == "__main__":
    main()
