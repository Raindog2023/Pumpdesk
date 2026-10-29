"""Find events where the decoded curve state is internally inconsistent.

For a genuine pump.fun TradeEvent:
  * virtual_sol_reserves - real_sol_reserves == 30 SOL (the classic offset)
  * virtual_token_reserves - real_token_reserves == 279.9e12 (the classic offset)
  * virtual_sol_reserves * virtual_token_reserves == K_STANDARD (constant product)

We check each of those per event and report which ones break, to separate
"genuinely custom curve" from "misparsed event".
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pumpdesk.curve import INITIAL_VSOL, K_STANDARD, VTOK_OFFSET, is_standard  # noqa: E402
from pumpdesk.decoder import decode_program_data  # noqa: E402


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/capture.jsonl")
    per = collections.defaultdict(list)
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        ev = decode_program_data(o["data"])
        if ev and ev["kind"] == "trade":
            per[ev["mint"]].append(ev)

    sol_off_bad = tok_off_bad = prod_bad = 0
    total = 0
    examples = []
    for mint, evs in per.items():
        for e in evs:
            total += 1
            v, t, rs, rt = e["vsol"], e["vtok"], e["rsol"], e["rtok"]
            sol_off = v - rs
            tok_off = t - rt
            if abs(sol_off - INITIAL_VSOL) > 10**8:
                sol_off_bad += 1
                if len(examples) < 8:
                    examples.append(("sol_offset", mint, e))
            if abs(tok_off - VTOK_OFFSET) > 10**11:
                tok_off_bad += 1
                if len(examples) < 16:
                    examples.append(("tok_offset", mint, e))
            if v and t and abs(v * t / K_STANDARD - 1) > 0.01:
                prod_bad += 1
                if len(examples) < 24:
                    examples.append(("product", mint, e))

    print(f"trade events: {total}")
    print(f"  bad sol offset (v-rs != 30 SOL): {sol_off_bad}")
    print(f"  bad token offset (t-rt != 279.9T): {tok_off_bad}")
    print(f"  bad constant product:             {prod_bad}")

    # per-token: how often does each invariant break, for tokens that start standard
    print("\ntokens starting standard, with breaks later:")
    shown = 0
    for mint, evs in per.items():
        if not is_standard(evs[0]["vsol"], evs[0]["vtok"], evs[0]["rtok"]):
            continue
        breaks = []
        for i, e in enumerate(evs):
            v, t, rs, rt = e["vsol"], e["vtok"], e["rsol"], e["rtok"]
            if abs(v - rs - INITIAL_VSOL) > 10**8:
                breaks.append((i, "sol_off", v - rs, v, t, rs, rt))
            elif abs(t - rt - VTOK_OFFSET) > 10**11:
                breaks.append((i, "tok_off", t - rt, v, t, rs, rt))
            elif v and t and abs(v * t / K_STANDARD - 1) > 0.01:
                breaks.append((i, "product", v * t / K_STANDARD, v, t, rs, rt))
        if breaks:
            shown += 1
            if shown <= 6:
                i, kind, val, v, t, rs, rt = breaks[0]
                print(f"  {mint[:12]} n={len(evs):3d} first break at event {i}/{len(evs)} kind={kind} "
                      f"val={val:.6g} v={v} t={t} rs={rs} rt={rt}")
    print(f"  total tokens starting standard that break later: {shown}")

    # distribution of the sol offset among 'weird' tokens
    print("\nsol offsets (v-rs) for tokens flagged nonstandard:")
    offs = collections.Counter()
    for mint, evs in per.items():
        if is_standard(evs[0]["vsol"], evs[0]["vtok"], evs[0]["rtok"]):
            continue
        for e in evs[:5]:
            offs[e["vsol"] - e["rsol"]] += 1
    for off, n in offs.most_common(8):
        print(f"  {off/1e9:.4f} SOL  x{n}")


if __name__ == "__main__":
    main()
