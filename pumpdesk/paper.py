"""Paper-trading engine: simulated fills on the real bonding curve.

Realism choices (all conservative):
- Orders fill only after `sim.latency_ms`, at the reserves seen THEN (not at signal time).
- pump.fun fees from the live trade events (protocol + creator), plus extra slippage
  and a fixed per-transaction cost.
- Our own orders don't move the simulated curve (fine for small sizes).
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from .curve import buy_with_budget, sell_net
from .rules import Rules


@dataclass
class Position:
    mint: str
    symbol: str
    tokens: int
    cost_sol: float          # remaining cost basis
    total_cost: float
    opened_at: float
    info: dict = field(default_factory=dict)
    proceeds: float = 0.0
    tp_done: bool = False
    exits: list = field(default_factory=list)
    peak_mult: float = 0.0
    last_mult: float = 1.0


@dataclass
class Order:
    mint: str
    side: str                # buy | sell
    fill_at: float
    budget: float = 0.0      # buy
    fraction: float = 1.0    # sell
    reason: str = ""
    info: dict = field(default_factory=dict)
    symbol: str = ""
    tx_sig: str = ""       # live trading: on-chain tx signature


class Paper:
    def __init__(self, rules: Rules, cash: float | None = None):
        self.rules = rules
        self.cash = rules.account.starting_sol if cash is None else cash
        self.positions: dict[str, Position] = {}
        self.pending: dict[str, Order] = {}
        self.closed: list[dict] = []
        self.traded: set[str] = set()
        self.events: list[dict] = []   # fills, drained by the app for alerts/logs

    # ---------- helpers
    def _slip(self) -> float:
        return 1 - self.rules.sim.extra_slippage_bps / 10_000

    def _proceeds(self, t, tokens: int) -> float:
        gross = sell_net(t.vsol, t.vtok, tokens, t.fee_bps) / 1e9 * self._slip()
        return gross - self.rules.sim.tx_cost_sol

    def mark_value(self, pos: Position, t) -> float:
        return max(0.0, self._proceeds(t, pos.tokens)) if pos.tokens > 0 else 0.0

    def mark_mult(self, pos: Position, t) -> float:
        return self.mark_value(pos, t) / pos.cost_sol if pos.cost_sol > 0 else 0.0

    def equity(self, tokens: dict) -> float:
        eq = self.cash + sum(o.budget for o in self.pending.values() if o.side == "buy")
        for m, pos in self.positions.items():
            t = tokens.get(m)
            eq += self.mark_value(pos, t) if t else pos.cost_sol
        return eq

    # ---------- entries
    def request_entry(self, t, now: float, equity: float, info: dict) -> bool:
        a = self.rules.account
        if t.mint in self.traded or t.mint in self.pending:
            return False
        if len(self.positions) + sum(o.side == "buy" for o in self.pending.values()) >= a.max_open_positions:
            return False
        budget = min(equity * a.risk_per_trade_pct / 100, self.cash)
        if budget <= self.rules.sim.tx_cost_sol * 4:
            return False
        self.cash -= budget
        self.traded.add(t.mint)
        self.pending[t.mint] = Order(t.mint, "buy", now + self.rules.sim.latency_ms / 1000,
                                     budget=budget, info=info, symbol=t.symbol)
        return True

    def request_exit(self, mint: str, now: float, reason: str = "MANUAL") -> bool:
        if mint not in self.positions:
            return False
        self.pending[mint] = Order(mint, "sell", now + self.rules.sim.latency_ms / 1000,
                                   fraction=1.0, reason=reason)
        return True

    # ---------- per-update processing
    def on_update(self, t, now: float):
        o = self.pending.get(t.mint)
        if o and now >= o.fill_at:
            self._fill(o, t, now)
        pos = self.positions.get(t.mint)
        if pos and t.mint not in self.pending:
            self._check_exits(pos, t, now)

    def tick(self, tokens: dict, now: float):
        for m, o in list(self.pending.items()):
            t = tokens.get(m)
            if now >= o.fill_at and (t or o.side == "sell"):
                    self._fill(o, t, now)
        for m, pos in list(self.positions.items()):
            t = tokens.get(m)
            if t and m not in self.pending:
                self._check_exits(pos, t, now)
            elif not t and m not in self.pending:
                # Token not in tracker (e.g. orphaned after engine restart) —
                # close at cost basis since we can't mark to market.
                self.pending[m] = Order(m, "sell", now, fraction=1.0, reason="STALE")

    def _check_exits(self, pos: Position, t, now: float):
        x = self.rules.exit
        if t.graduated and x.exit_on_graduation:
            self._fill(Order(t.mint, "sell", now, fraction=1.0, reason="GRADUATED"), t, now)
            return
        mult = self.mark_mult(pos, t)
        pos.last_mult = mult
        pos.peak_mult = max(pos.peak_mult, mult)
        reason, frac = None, 1.0
        if mult <= 1 - x.stop_loss_pct / 100:
            reason = "STOP"
        elif pos.tp_done and x.breakeven_after_tp and mult <= 1.0:
            reason = "BREAKEVEN"
        elif not pos.tp_done and mult >= x.take_profit_multiple:
            reason, frac = "TP", x.take_profit_sell_pct / 100
        elif now - pos.opened_at >= x.max_hold_min * 60:
            reason = "TIME"
        elif now - t.last_trade >= x.stale_exit_min * 60:
            reason = "STALE"
        if reason:
            self.pending[t.mint] = Order(t.mint, "sell", now + self.rules.sim.latency_ms / 1000,
                                         fraction=frac, reason=reason)

    def _fill(self, o: Order, t, now: float):
        self.pending.pop(o.mint, None)
        if o.side == "buy":
            spend = o.budget - self.rules.sim.tx_cost_sol
            toks = int(buy_with_budget(t.vsol, t.vtok, int(spend * 1e9), t.fee_bps) * self._slip())
            if toks <= 0:
                self.cash += o.budget
                return
            self.positions[o.mint] = Position(o.mint, o.symbol or t.symbol, toks, o.budget, o.budget, now, info=o.info)
            self.events.append({"type": "entry", "mint": o.mint, "symbol": t.symbol, "sol": o.budget,
                                "price": t.price, "mcap_sol": t.mcap_sol, "curve_pct": t.curve_pct, "at": now})
            return
        pos = self.positions.get(o.mint)
        if not pos:
            return
        q = pos.tokens if o.fraction >= 1 else int(pos.tokens * o.fraction)
        if t:
            proceeds = max(0.0, self._proceeds(t, q))
        else:
            # Token not tracked (orphaned after restart) — close at cost basis
            proceeds = pos.cost_sol * q / pos.tokens if pos.tokens > 0 else 0.0
        self.cash += proceeds
        pos.proceeds += proceeds
        pos.cost_sol -= pos.cost_sol * q / pos.tokens
        pos.tokens -= q
        pos.exits.append(o.reason)
        if o.reason == "TP":
            pos.tp_done = True
        self.events.append({"type": "exit", "mint": o.mint, "symbol": pos.symbol, "reason": o.reason,
                            "sol": proceeds, "partial": pos.tokens > 0, "at": now})
        if pos.tokens <= 0 or o.fraction >= 1:
            del self.positions[o.mint]
            pnl = pos.proceeds - pos.total_cost
            self.closed.append({
                "mint": pos.mint, "symbol": pos.symbol,
                "opened_at": pos.opened_at, "closed_at": now,
                "hold_min": round((now - pos.opened_at) / 60, 2),
                "cost_sol": round(pos.total_cost, 6), "proceeds_sol": round(pos.proceeds, 6),
                "pnl_sol": round(pnl, 6), "pnl_pct": round(pnl / pos.total_cost * 100, 2),
                "peak_mult": round(pos.peak_mult, 3),
                "exit_reason": "+".join(pos.exits), **{f"entry_{k}": v for k, v in pos.info.items()},
            })

    # ---------- persistence
    def to_state(self) -> dict:
        return {"cash": self.cash, "positions": [asdict(p) for p in self.positions.values()],
                "pending": [asdict(o) for o in self.pending.values()],
                "traded": sorted(self.traded)[-5000:], "saved_at": time.time()}

    @classmethod
    def from_state(cls, rules: Rules, st: dict) -> Paper:
        p = cls(rules, cash=st["cash"])
        for d in st.get("positions", []):
            p.positions[d["mint"]] = Position(**d)
        for d in st.get("pending", []):
            if d["side"] == "buy":      # stale buy orders are refunded on restart
                p.cash += d["budget"]
        p.traded = set(st.get("traded", []))
        return p
