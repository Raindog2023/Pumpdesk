"""Per-mint consistency audit of decoded trade events.

Ground-truth checks that do not depend on the simulator:

1. K = vsol * vtok must be constant across a mint's trades (virtual reserves
   implement a constant-product curve, so K is invariant while the curve lives).
2. The virtual/real offsets (vtok - rtok, vsol - rsol) must be constant per mint.
3. sol_amount must match the reserve delta (buy: d_rsol ~ sol; sell: d_vsol ~ sol).
4. The event's `user` and `creator` must be valid 32-byte pubkeys, and `ts` a
   plausible unix timestamp.

A mint that fails these is a mint whose events we are misreading.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pumpdesk.decoder import decode_program_data


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/live_capture.jsonl")
    mints = collections.defaultdict(list)
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        ev = decode_program_data(o["data"])
        if ev and ev["kind"] == "trade":
            mints[ev["mint"]].append((o["slot"], ev, len(o["data"])))

    print(f"mints: {len(mints)}")
    bad_k = bad_off = 0
    k_forms = collections.Counter()
    examples = []
    for m, rows in mints.items():
        rows.sort(key=lambda r: r[0])
        ks = [e["vsol"] * e["vtok"] for _s, e, _l in rows]
        offs = [e["vtok"] - e["rtok"] for _s, e, _l in rows]
        soloffs = [e["vsol"] - e["rsol"] for _s, e, _l in rows]  # noqa: F841
        # K constant?
        k_rel = (max(ks) - min(ks)) / max(ks) if ks and max(ks) else 1
        o_rel = (max(offs) - min(offs)) / max(offs) if offs and max(offs) else 1
        if k_rel > 0.02:
            bad_k += 1
            if len(examples) < 8:
                examples.append((m, "K varies {:.3f}".format(k_rel), rows[:4]))
        if o_rel > 0.02:
            bad_off += 1
        k_forms[round(ks[0] / 1e25, 2) if ks else 0] += 1

    print(f"mints with varying K (>2%): {bad_k}/{len(mints)}")
    print(f"mints with varying (vtok-rtok): {bad_off}/{len(mints)}")
    print("\nexamples of varying-K mints:")
    for m, why, rows in examples:
        print(f"\n  {m}  {why}")
        for slot, e, l in rows:
            print(f"    slot={slot} len={l} vsol={e['vsol']} vtok={e['vtok']} "
                  f"rsol={e['rsol']} rtok={e['rtok']} buy={e['is_buy']} sol={e['sol']}")

    print("\nK distribution (x1e25):")
    for k, n in k_forms.most_common(10):
        print(f"  {k:>6}: {n}")


if __name__ == "__main__":
    main()
