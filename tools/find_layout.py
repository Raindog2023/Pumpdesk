"""Search each trade-event blob for the true reserve-field offsets.

pump.fun's TradeEvent keeps the same 8-byte discriminator across program
versions, but the struct layout differs between them. Our decoder assumes one
fixed layout, so a layout change silently yields wrong numbers.

The pump.fun bonding curve gives us a way to locate the fields without an IDL:
for every trade the invariant

    virtual_token_reserves - real_token_reserves == 279.9e12   (VTOK_OFFSET)

holds, and the four reserves sit next to each other. We scan EVERY byte offset
(the `is_buy` bool shifts the tail by 1, so the reserves are not 8-aligned)
for a pair (i, i+16) satisfying that identity, then read the neighbouring two
u64s as the SOL side and check the constant product against K_STANDARD.
"""
from __future__ import annotations

import base64
import collections
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pumpdesk.curve import K_STANDARD, VTOK_OFFSET  # noqa: E402

TOL = 10**11
TRADE_DISC = bytes.fromhex("bddb7fd34ee661ee")


def u64_at(raw: bytes, i: int) -> int:
    return struct.unpack_from("<Q", raw, i)[0]


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/live_capture.jsonl")
    sizes = collections.Counter()
    found = collections.Counter()
    layouts = collections.Counter()
    examples = {}
    total = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        raw = base64.b64decode(o["data"])
        if raw[:8] != TRADE_DISC:
            continue
        total += 1
        sizes[len(raw)] += 1

        hits = []
        for i in range(8, len(raw) - 24):
            vt = u64_at(raw, i)
            if vt < VTOK_OFFSET:
                continue
            if abs((vt - u64_at(raw, i + 16)) - VTOK_OFFSET) < TOL:
                hits.append(i)
        if not hits:
            found["no_candidate"] += 1
            continue
        best = None
        for i in hits:
            vt = u64_at(raw, i)
            for so in (i - 8, i + 8):
                if so < 8:
                    continue
                vs = u64_at(raw, so)
                k = vs * vt
                if not k:
                    continue
                ratio = k / K_STANDARD
                if 0.98 < ratio < 1.02:
                    best = (i, so, ratio, "std")
                    break
            if best:
                break
        if best is None:
            i = hits[0]
            so = i - 8 if i - 8 >= 8 else i + 8
            vs = u64_at(raw, so)
            best = (i, so, (vs * u64_at(raw, i)) / K_STANDARD if vs else 0, "nonstd")
            found["no_standard_product"] += 1
        else:
            found["standard_product"] += 1

        key = (len(raw), best[0], best[1])
        layouts[key] += 1
        examples.setdefault(key, (raw, o["slot"], best))

    print(f"trade blobs: {total}")
    print("\nraw blob sizes:")
    for s, n in sorted(sizes.items()):
        print(f"  {s:>4} bytes x{n}")

    print("\nclassifications:")
    for k, n in found.items():
        print(f"  {k:>24}: {n}")

    print("\nlayout histogram (raw_size, vtok_byte, vsol_byte) -> count:")
    for k, n in sorted(layouts.items()):
        print(f"  {k} -> {n}")

    print("\nexamples:")
    for key, (raw, slot, d) in sorted(examples.items()):
        i, so, ratio, kind = d
        print(f"\n  raw_size={key[0]} vtok@{i} vsol@{so} K/Kstd={ratio:.4f} ({kind}) slot={slot}")
        print(f"    vtok={u64_at(raw, i)} rtok={u64_at(raw, i + 16)} vsol={u64_at(raw, so)}")
        print(f"    hex[80:140]={raw[80:140].hex()}")


if __name__ == "__main__":
    main()
