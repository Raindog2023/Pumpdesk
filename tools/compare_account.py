"""Compare decoded trade events against the on-chain bonding curve account.

For a set of mints seen in the feed we fetch the bonding curve PDA
(seeds ["bonding-curve", mint]) and dump its raw bytes, so we can see the
account layout and compare the account's reserves with the event's reserves.
"""
from __future__ import annotations

import base64
import json
import struct
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pumpdesk.decoder import decode_program_data  # noqa: E402

RPC = "https://api.mainnet-beta.solana.com"
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58dec(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + B58.index(ch)
    return n.to_bytes(32, "big")


def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(RPC, data=body, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def find_pda(mint_b58: str) -> str | None:
    """Brute-force is impossible; use the program's known PDA seeds via RPC."""
    seeds = [b"bonding-curve", b58dec(mint_b58)]
    for bump in range(255, 250, -1):
        pass
    # fall back to scanning program accounts is too heavy; use getProgramAccounts
    return None


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/live_capture.jsonl")
    mints = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        o = json.loads(line)
        ev = decode_program_data(o["data"])
        if ev and ev["kind"] == "trade" and ev["mint"] not in mints:
            mints.append(ev["mint"])
        if len(mints) >= 8:
            break

    print(f"mints: {mints}\n")
    res = rpc("getProgramAccounts", [PUMP, {"encoding": "base64", "dataSlice": {"offset": 0, "length": 8}}])
    accounts = res.get("result") or []
    print(f"pump program accounts: {len(accounts)}")

    # bonding curve account = anchor account with discriminator of BondingCurve
    # anchor discriminator = sha256("account:BondingCurve")[:8]
    import hashlib
    disc = hashlib.sha256(b"account:BondingCurve").digest()[:8]
    print(f"BondingCurve discriminator: {disc.hex()}")

    # fetch full data for accounts with that discriminator
    res = rpc("getProgramAccounts", [PUMP, {"encoding": "base64",
                                           "filters": [{"memcmp": {"offset": 0, "bytes": base64.b64encode(disc).decode()}}]}])
    bcs = res.get("result") or []
    print(f"bonding curve accounts: {len(bcs)}")
    if not bcs:
        return

    # map: find the mint inside each account (account holds mint at offset 8..40)
    by_mint = {}
    for a in bcs:
        raw = base64.b64decode(a["account"]["data"][0])
        if len(raw) >= 40:
            mint = raw[8:40]
            by_mint[base64.b16encode(mint).decode()] = raw

    for m in mints[:8]:
        raw = by_mint.get(base64.b16encode(b58dec(m)).decode())
        if raw is None:
            # anchor Borsh: skip 8-byte disc then mint pubkey
            continue
        print(f"\n=== {m} account len={len(raw)} ===")
        print(f"  disc={raw[:8].hex()}")
        print(f"  mint@8 ={raw[8:40].hex()}")
        for off in range(40, min(len(raw), 160), 8):
            if off + 8 > len(raw):
                break
            v = struct.unpack_from("<Q", raw, off)[0]
            if v:
                print(f"  u64@{off:<4} = {v}")


if __name__ == "__main__":
    main()
