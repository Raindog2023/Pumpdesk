"""Decode pump.fun program events from Solana `Program data:` log lines.

Only the stable leading fields of each event are parsed, so newer program
versions that append fields keep working.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import struct

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
TOKEN_DECIMALS = 6
TOTAL_SUPPLY_RAW = 1_000_000_000 * 10**TOKEN_DECIMALS
LAMPORTS = 1_000_000_000

_ALPH = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _ALPH[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


def _disc(name: str) -> bytes:
    return hashlib.sha256(f"event:{name}".encode()).digest()[:8]


TRADE, CREATE, COMPLETE = _disc("TradeEvent"), _disc("CreateEvent"), _disc("CompleteEvent")


class _R:
    def __init__(self, raw: bytes):
        self.raw, self.o = raw, 8

    def pk(self) -> str:
        v = b58encode(self.raw[self.o:self.o + 32]); self.o += 32; return v

    def u64(self) -> int:
        v = struct.unpack_from("<Q", self.raw, self.o)[0]; self.o += 8; return v

    def i64(self) -> int:
        v = struct.unpack_from("<q", self.raw, self.o)[0]; self.o += 8; return v

    def u8(self) -> int:
        v = self.raw[self.o]; self.o += 1; return v

    def s(self) -> str:
        n = struct.unpack_from("<I", self.raw, self.o)[0]
        v = self.raw[self.o + 4:self.o + 4 + n].decode("utf-8", "replace"); self.o += 4 + n; return v


def decode_program_data(b64: str) -> dict | None:
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        return None
    if len(raw) < 8:
        return None
    head = raw[:8]
    try:
        r = _R(raw)
        if head == TRADE:
            ev = {"kind": "trade", "mint": r.pk(), "sol": r.u64(), "tokens": r.u64(),
                  "is_buy": r.u8() == 1, "user": r.pk(), "ts": r.i64(),
                  "vsol": r.u64(), "vtok": r.u64(), "rsol": r.u64(), "rtok": r.u64()}
            r.pk()  # fee recipient
            ev["fee_bps"] = r.u64(); r.u64()
            ev["creator"] = r.pk()
            ev["creator_fee_bps"] = r.u64()
            return ev
        if head == CREATE:
            ev = {"kind": "create", "name": r.s(), "symbol": r.s(), "uri": r.s(),
                  "mint": r.pk(), "bonding_curve": r.pk(), "user": r.pk(), "creator": r.pk()}
            return ev
        if head == COMPLETE:
            r.pk()  # user
            return {"kind": "complete", "mint": r.pk()}
    except (struct.error, IndexError):
        return None
    return None

