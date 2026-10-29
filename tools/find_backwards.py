"""Find events that move a token's curve state backwards.

For a given mint the bonding curve only advances (rtok decreases, vsol grows).
If rtok jumps back up to ~INITIAL_REAL_TOKENS mid-life, either the decoder
mis-read that event, or two different programs/events are being mixed up.

Prints the raw hex of the offending event and the event before it so the two
can be compared byte by byte.
"""
from __future__ import annotations

import base64
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pumpdesk.curve import INITIAL_REAL_TOKENS, INITIAL_VSOL, INITIAL_VTOK  # noqa: E402
from pumpdesk.decoder import decode_program_data  # noqa: E402


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/live_capture.jsonl")
    per = collections.defaultdict(list)
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        ev = decode_program_data(o.get("data", ""))
        if ev and ev["kind"] == "trade":
            per[ev["mint"]].append((o, ev))

    print(f"mints with trades: {len(per)}")
    back = 0
    resets = 0
    shown = 0
    for mint, rows in per.items():
        rows.sort(key=lambda r: r[0].get("rx", 0))
        prev_rt = None
        for o, ev in rows:
            rt = ev["rtok"]
            if prev_rt is not None and rt > prev_rt:
                back += 1
                if rt >= INITIAL_REAL_TOKENS * 0.99:
                    resets += 1
            prev_rt = rt

    print(f"events where rtok moved BACKWARD: {back}")
    print(f"  of which a reset to ~initial ({INITIAL_REAL_TOKENS}): {resets}")

    # detailed dump of the first few backward moves
    print("\n--- first backward moves (full decoded fields + raw hex head) ---")
    shown = 0
    for mint, rows in per.items():
        rows.sort(key=lambda r: r[0].get("rx", 0))
        for i in range(1, len(rows)):
            (o1, e1), (o2, e2) = rows[i - 1], rows[i]
            if e2["rtok"] > e1["rtok"]:
                if shown >= 5:
                    return
                shown += 1
                raw1 = base64.b64decode(o1["data"])
                raw2 = base64.b64decode(o2["data"])
                print(f"\nmint {mint}")
                print(f"  prev: rtok={e1['rtok']} vsol={e1['vsol']} vtok={e1['vtok']} "
                      f"rsol={e1['rsol']} len={len(raw1)} slot={o1.get('slot')}")
                print(f"  next: rtok={e2['rtok']} vsol={e2['vsol']} vtok={e2['vtok']} "
                      f"rsol={e2['rsol']} len={len(raw2)} slot={o2.get('slot')}")
                print(f"  next raw hex: {raw2.hex()[:200]}")
                print(f"  is_buy={e2['is_buy']} sol={e2['sol']} tokens={e2['tokens']} "
                      f"fee_bps={e2['fee_bps']} creator_fee_bps={e2['creator_fee_bps']}")
                # compare layout: does the previous event have the same byte length?
                print(f"  same length as prev: {len(raw1) == len(raw2)}")


if __name__ == "__main__":
    main()
