"""Attribute each 'Program data:' line to the program that actually emitted it.

Solana logs form a stack: 'Program X invoke [depth]' pushes X, 'Program X
success' pops it, and 'Program data:' lines emitted while X is on top belong
to X. A naive 'last invoke seen' attribution is wrong because a CPI'd inner
program's success line precedes the outer program's data lines.

Why this matters: Anchor event discriminators are sha256("event:<Name>")[:8],
derived from the NAME only. Two different programs can both emit an event
called "TradeEvent" and share an identical 8-byte discriminator while having
completely different field layouts. Decoding such an event with pump.fun's
layout yields plausible-looking garbage.
"""
from __future__ import annotations

import base64
import collections
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pumpdesk.curve import INITIAL_VSOL, K_STANDARD, VTOK_OFFSET
from pumpdesk.decoder import decode_program_data

PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
TRADE = hashlib.sha256(b"event:TradeEvent").digest()[:8]
CREATE = hashlib.sha256(b"event:CreateEvent").digest()[:8]
COMPLETE = hashlib.sha256(b"event:CompleteEvent").digest()[:8]


def attribute(logs: list[str]):
    """Yield (program_id, data_b64) with a proper invoke-stack attribution."""
    stack: list[str] = []
    for line in logs:
        if line.startswith("Program data: "):
            yield (stack[-1] if stack else None), line[14:]
        elif line.startswith("Program "):
            parts = line.split()
            if len(parts) >= 4 and parts[2] == "invoke":
                try:
                    depth = int(parts[3].strip("[]"))
                except ValueError:
                    continue
                # keep the stack aligned to the announced depth
                while len(stack) > depth - 1:
                    stack.pop()
                stack.append(parts[1])
            elif len(parts) >= 3 and parts[2] == "success":
                if stack:
                    stack.pop()


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/raw_logs.jsonl")
    by_prog = collections.Counter()
    trade_by_prog = collections.Counter()
    other_prog_trades = collections.Counter()
    suspect_examples = []
    consistent = collections.Counter()
    total = collections.Counter()

    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        for prog, b64 in attribute(o["logs"]):
            by_prog[prog] += 1
            try:
                raw = base64.b64decode(b64, validate=True)
            except Exception:  # noqa: BLE001, S112
                continue
                continue
            total[prog] += 1
            ev = decode_program_data(b64)
            if not ev:
                continue
            trade_by_prog[prog] += 1
            v, t, rs, rt = ev["vsol"], ev["vtok"], ev["rsol"], ev["rtok"]
            ok_tok_offset = abs(t - rt - VTOK_OFFSET) < 10**11
            ok_product = bool(v and t) and abs(v * t / K_STANDARD - 1) < 0.01
            ok_sol_offset = abs(v - rs - INITIAL_VSOL) < 10**9
            if ok_tok_offset and ok_product and ok_sol_offset:
                consistent[prog] += 1
            if prog != PUMP:
                other_prog_trades[prog] += 1
                if len(suspect_examples) < 6:
                    suspect_examples.append((prog, ev, len(raw), o["slot"], o["sig"], b64))

    print("TRADE-discriminator events by emitting program:")
    for prog, n in total.most_common():
        print(f"  {str(prog)[:44]:46} trade_disc={n:>6} decoded={trade_by_prog[prog]:>6} "
              f"standard_shape={consistent[prog]:>6} non_pump={prog != PUMP}")

    print(f"\nNON-PUMP programs emitting TradeEvent-disc events: "
          f"{sum(other_prog_trades.values())} of {sum(total.values())} total")

    print("\nexamples of non-pump TradeEvent events:")
    for prog, ev, ln, slot, sig, b64 in suspect_examples:
        print(f"\n  program={prog}")
        print(f"    mint={ev['mint']} is_buy={ev['is_buy']} sol={ev['sol']} tokens={ev['tokens']}")
        print(f"    vsol={ev['vsol']} vtok={ev['vtok']} rsol={ev['rsol']} rtok={ev['rtok']}")
        print(f"    len={ln} slot={slot} sig={sig[:20]}")
        raw = base64.b64decode(b64)
        print(f"    hex={raw.hex()[:180]}")


if __name__ == "__main__":
    main()
