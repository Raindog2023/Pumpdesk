"""Fetch the pump.fun program's on-chain Anchor IDL and print its event layouts.

Ground truth for the decode: `anchor:idl` accounts are 8-byte discriminator
sha256("anchor:idl")[:8] followed by the IDL, either as raw JSON (older
programs) or zlib-compressed JSON. We dump every event struct with its field
offsets so we can check our fixed-offset decoder against the real layout.
"""
from __future__ import annotations

import base64
import json
import urllib.request

RPC = "https://api.mainnet-beta.solana.com"
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
IDL_DISC = base64.b64decode("X45O9Vj0AiI=")  # sha256("anchor:idl")[:8]


def rpc(method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(RPC, data=body, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def main():
    res = rpc("getProgramAccounts", [PUMP, {
        "encoding": "base64",
        "filters": [{"memcmp": {"offset": 0, "bytes": base64.b64encode(IDL_DISC).decode()}}],
    }])
    vals = res.get("result") or []
    print(f"anchor:idl accounts found: {len(vals)}")
    if not vals:
        res = rpc("getProgramAccounts", [PUMP, {"encoding": "base64"}])
        vals = res.get("result") or []
        print(f"total program accounts: {len(vals)}")
        for v in vals[:10]:
            raw = base64.b64decode(v["account"]["data"][0])
            print(f"  len={len(raw)} head={raw[:8].hex()}")
        return

    for v in vals:
        raw = base64.b64decode(v["account"]["data"][0])
        body = raw[8:]
        try:
            import zlib
            body = zlib.decompress(body)
        except Exception:  # noqa: BLE001, S110
            pass
        try:
            idl = json.loads(body)
        except Exception as e:  # noqa: BLE001
            print(f"  could not parse idl: {e}; head={body[:120]!r}")
            continue
        print(f"  idl name={idl.get('name')} version={idl.get('version')} "
              f"instructions={len(idl.get('instructions', []))} types={len(idl.get('types', []))}")
        for ev in idl.get("events", []):
            print(f"\n  EVENT {ev.get('name')}")
            offset = 0
            for f in ev.get("fields", []):
                t = f["type"]
                size = {"pubkey": 32, "u64": 8, "i64": 8, "u8": 1, "bool": 1,
                        "u32": 4, "u16": 2, "i32": 4}.get(t)
                if size is None:
                    tn = t["name"]
                    size = 8 if tn in ("u64", "i64") else 32
                print(f"    +{offset:<4} {f['name']:28} {t}")
                offset += size
            print(f"    total payload = {offset} (+8 discriminator = {offset + 8})")


if __name__ == "__main__":
    main()
