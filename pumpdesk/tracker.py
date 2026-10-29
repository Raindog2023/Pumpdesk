"""Per-token state rebuilt purely from pump.fun on-chain events."""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from .curve import is_standard, price_sol, progress_pct
from .decoder import TOTAL_SUPPLY_RAW
from .filters import Facts


@dataclass
class Token:
    mint: str
    symbol: str
    name: str
    creator: str
    create_slot: int
    born: float
    holders: dict = field(default_factory=dict)  # wallet -> raw token balance
    block0: set = field(default_factory=set)
    buyers: set = field(default_factory=set)
    dev_sold_at_min: float | None = None
    vsol: int = 30 * 10**9
    vtok: int = 1_073_000_000 * 10**6
    rtok: int = 793_100_000_000_000
    fee_bps: int = 125
    trades: int = 0
    buys: int = 0
    vol_sol: float = 0.0
    last_trade: float = 0.0
    ath_price: float = 0.0
    graduated: bool = False
    nonstandard: bool = False
    status: str = "NEW"
    reasons: list = field(default_factory=list)

    @property
    def dev_pct(self) -> float:
        return max(0, self.holders.get(self.creator, 0)) / TOTAL_SUPPLY_RAW * 100

    @property
    def block0_buyers(self) -> int:
        return len(self.block0)

    @property
    def curve_pct(self) -> float:
        return progress_pct(self.rtok)

    @property
    def price(self) -> float:
        return price_sol(self.vsol, self.vtok)

    @property
    def mcap_sol(self) -> float:
        return self.price * 1_000_000_000

    def top10_pct(self) -> float:
        top = heapq.nlargest(10, (b for b in self.holders.values() if b > 0))
        return sum(top) / TOTAL_SUPPLY_RAW * 100

    def facts(self, now: float, local_hour: int, dev_window_min: float) -> Facts:
        sold_early = self.dev_sold_at_min is not None and self.dev_sold_at_min <= dev_window_min
        return Facts(curve_pct=self.curve_pct, top10_pct=self.top10_pct(), dev_pct=self.dev_pct,
                     dev_sold_early=sold_early, block0_buyers=self.block0_buyers,
                     age_min=(now - self.born) / 60, local_hour=local_hour,
                     nonstandard_curve=self.nonstandard)


class Tracker:
    def __init__(self):
        self.tokens: dict[str, Token] = {}

    def on_event(self, ev: dict, slot: int, now: float) -> Token | None:
        kind = ev["kind"]
        if kind == "create":
            if ev["mint"] not in self.tokens:
                self.tokens[ev["mint"]] = Token(ev["mint"], ev["symbol"][:16], ev["name"][:40],
                                                ev["creator"], slot, now, last_trade=now)
            return self.tokens[ev["mint"]]
        tok = self.tokens.get(ev["mint"])
        if tok is None:
            return None
        if kind == "complete":
            tok.graduated = True
            return tok
        user, amt = ev["user"], ev["tokens"]
        tok.holders[user] = tok.holders.get(user, 0) + (amt if ev["is_buy"] else -amt)
        if tok.holders[user] <= 0:
            tok.holders.pop(user)
        if ev["is_buy"]:
            tok.buys += 1
            if user != tok.creator:
                tok.buyers.add(user)
                if slot == tok.create_slot:
                    tok.block0.add(user)
        elif user == tok.creator and tok.dev_sold_at_min is None:
            tok.dev_sold_at_min = (now - tok.born) / 60
        tok.vsol, tok.vtok, tok.rtok = ev["vsol"], ev["vtok"], ev["rtok"]
        if not is_standard(tok.vsol, tok.vtok, tok.rtok):
            tok.nonstandard = True
        tok.fee_bps = ev["fee_bps"] + ev["creator_fee_bps"]
        tok.trades += 1
        tok.vol_sol += ev["sol"] / 1e9
        tok.last_trade = now
        tok.ath_price = max(tok.ath_price, tok.price)
        return tok

    def prune(self, now: float, idle_s: float, keep: set) -> int:
        dead = [m for m, t in self.tokens.items() if m not in keep and now - t.last_trade > idle_s]
        for m in dead:
            del self.tokens[m]
        return len(dead)
