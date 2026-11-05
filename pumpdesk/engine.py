"""Glue: tracker -> filters -> paper engine -> journal/alerts. No network here."""
from __future__ import annotations

import csv
import json
import time
from collections import Counter, deque
from datetime import datetime
from pathlib import Path

from .filters import evaluate
from .live import LiveTrader, LiveConfig, Wallet
from .paper import Paper
from .rules import Rules
from .tracker import Tracker

FINAL = ("PASS", "FAIL", "EXPIRED")
TOKEN_FIELDS = [
    "time", "mint", "symbol", "status", "reasons", "curve_pct", "mcap_sol",
    "top10_pct", "dev_pct", "block0", "buyers", "age_min"
]
TRADE_FIELDS = [
    "opened", "closed", "open_hour", "mint", "symbol", "hold_min", "cost_sol",
    "proceeds_sol", "pnl_sol", "pnl_pct", "peak_mult", "exit_reason",
    "entry_curve_pct", "entry_top10_pct", "entry_dev_pct", "entry_block0",
    "entry_buyers", "entry_age_min", "entry_mcap_sol"
]


def _ts(t: float) -> str:
    return datetime.fromtimestamp(t).isoformat(timespec="seconds")


class Engine:
    def __init__(self, rules: Rules, data_dir: Path, live_config: LiveConfig | None = None):
        self.rules = rules
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.tracker = Tracker()
        self.live_config = live_config
        self.trader = self._load_trader()
        self.closed: list[dict] = self._load_closed()
        self.paused = False
        self.decisions: deque[dict] = deque(maxlen=300)
        self.alerts: list[str] = []
        self.stats = Counter()
        self.fail_reasons = Counter()
        self.started = time.time()
        
        # In-memory CSV write buffer to eliminate blocking I/O on hot path
        self._csv_buffer: list[tuple[Path, list[str], dict]] = []

    @property
    def paper(self):
        """Backward compatibility for external property lookups."""
        return self.trader

    def _append_csv(self, path: Path, fields: list[str], row: dict):
        self._csv_buffer.append((path, fields, row))

    def _flush_csv(self):
        if not self._csv_buffer:
            return
        to_write = self._csv_buffer[:]
        self._csv_buffer.clear()
        
        # Group writes by destination path
        by_path: dict[Path, list[tuple[list[str], dict]]] = {}
        for p, f, r in to_write:
            by_path.setdefault(p, []).append((f, r))

        for p, entries in by_path.items():
            new = not p.exists()
            with p.open("a", newline="", encoding="utf-8") as f:
                writer = None
                for fields, row in entries:
                    if writer is None:
                        writer = csv.DictWriter(f, fields, extrasaction="ignore")
                        if new:
                            writer.writeheader()
                            new = False
                    writer.writerow(row)

    def _load_trader(self):
        p = self.dir / "state.json"
        if p.exists():
            try:
                st = json.loads(p.read_text(encoding="utf-8"))
                if self.live_config is not None:
                    tr = LiveTrader(self.rules, self.live_config, self.dir)
                    tr.from_state(st)
                    return tr
                else:
                    return Paper.from_state(self.rules, st)
            except Exception:
                p.rename(self.dir / f"state.bad.{int(time.time())}.json")

        if self.live_config is not None:
            return LiveTrader(self.rules, self.live_config, self.dir)
        return Paper(self.rules)

    def _load_closed(self) -> list[dict]:
        p = self.dir / "trades.csv"
        if not p.exists():
            return []
        try:
            with p.open(encoding="utf-8") as f:
                return list(csv.DictReader(f))
        except Exception:
            return []

    def set_rules(self, rules: Rules):
        self.rules = rules
        self.trader.rules = rules

    def save(self):
        self._flush_csv()
        p = self.dir / "state.json"
        tmp = self.dir / "state.tmp"
        tmp.write_text(json.dumps(self.trader.to_state(), indent=2), encoding="utf-8")
        tmp.replace(p)

    def reset_account(self):
        self.save()
        if (self.dir / "trades.csv").exists():
            (self.dir / "trades.csv").rename(
                self.dir / f"trades.{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
            )
        self.closed.clear()
        self.trader = Paper(self.rules)
        self.save()

    def handle(self, slot: int, ev: dict, now: float, local_hour: int):
        self.stats["events"] += 1
        tok = self.tracker.on_event(ev, slot, now)
        if tok is None:
            return

        if ev.get("kind") == "trade" and tok.status not in FINAL:
            f = tok.facts(now, local_hour, self.rules.entry.dev_sell_window_min)
            d = evaluate(f, self.rules)
            changed = d.status != tok.status
            tok.status, tok.reasons = d.status, d.reasons

            if changed and (d.status in FINAL or d.status == "QUIET"):
                self._log_decision(tok, f, now)

            if d.status == "PASS" and not self.paused:
                eq = self.trader.equity(self.tracker.tokens)
                if not self.trader.request_entry(tok, now, eq, info=f):
                    self.stats["pass_skipped"] += 1

        if tok.mint in self.trader.positions or tok.mint in self.trader.pending:
            self.trader.on_update(tok, now)
            self._drain_trader()

    def tick(self, now: float):
        self.trader.tick(now, self.tracker.tokens)
        self._drain_trader()
        keep = set(self.trader.positions) | set(self.trader.pending)
        self.stats["pruned"] += self.tracker.prune(now, idle_s=15 * 60, keep=keep)

    def _log_decision(self, tok, f: dict, now: float):
        self.stats[f"status_{tok.status}"] += 1
        for r in tok.reasons:
            self.fail_reasons[r.split(":")[0].split("<")[0]] += 1

        row = {
            "time": _ts(now),
            "mint": tok.mint,
            "symbol": tok.symbol,
            "status": tok.status,
            "reasons": ";".join(tok.reasons),
            "curve_pct": round(f.curve_pct, 1),
            "mcap_sol": round(f.mcap_sol, 1),
            "top10_pct": round(f.top10_pct, 1),
            "dev_pct": round(f.dev_pct, 2),
            "block0": f.block0_buyers,
            "buyers": len(tok.buyers),
            "age_min": round(f.age_min, 1),
        }
        self.decisions.appendleft(row)
        self._append_csv(self.dir / "tokens.csv", TOKEN_FIELDS, row)

    def _drain_trader(self):
        if not self.trader.events and not self.trader.closed:
            return

        is_live = self.live_config is not None
        tag = "🔴 LIVE" if is_live else "🟢 PAPER"

        for e in self.trader.events:
            link = f"https://pump.fun/coin/{e['mint']}"
            if e["type"] == "buy":
                self.alerts.append(
                    f"{tag} BUY [{e['symbol']}] \n"
                    f"Cost: {e['sol']:.3f} SOL @ {e.get('mcap_sol', 0):.1f} SOL mcap\n"
                    f"Curve: {e.get('curve_pct', 0):.0f}% | {link}"
                )
            elif e["type"] in ("exit", "partial"):
                pfx = "PARTIAL " if e["type"] == "partial" else ""
                self.alerts.append(
                    f"{tag} {pfx}SELL [{e['symbol']}] ({e['reason']})\n"
                    f"Proceeds: {e['sol']:.3f} SOL | {link}"
                )
        self.trader.events.clear()

        for c in self.trader.closed:
            row = {
                **c,
                "opened": _ts(c["opened_at"]),
                "closed": _ts(c["closed_at"]),
                "open_hour": datetime.fromtimestamp(c["opened_at"]).hour,
            }
            self._append_csv(self.dir / "trades.csv", TRADE_FIELDS, row)
            self.closed.append({k: row.get(k) for k in TRADE_FIELDS})
            pnl_sol = float(c.get("pnl_sol", 0))
            pnl_pct = float(c.get("pnl_pct", 0))
            self.alerts.append(
                f"{tag} CLOSED [{c['symbol']}]: {pnl_sol:+.4f} SOL ({pnl_pct:+.1f}%) "
                f"via {c['exit_reason']}, held {c.get('hold_min', 0):.1f}m"
            )
        self.trader.closed.clear()

    def drain_alerts(self) -> list[str]:
        al = self.alerts[:]
        self.alerts.clear()
        return al

    def snapshot(self, now: float) -> dict:
        toks = self.tracker.tokens
        account = self.trader.account
        start = float(self.trader.starting_sol or 1.0)
        pnls = [float(c.get("pnl_sol") or 0) for c in self.closed]
        wins = [p for p in pnls if p > 0]

        positions = []
        for m, p in self.trader.positions.items():
            t = toks.get(m)
            mult = paper_mark_mult(p, t) if (t and "paper_mark_mult" in globals()) else 1.0
            positions.append({
                "mint": m,
                "symbol": p.symbol,
                "cost": round(p.cost_sol, 4),
                "value": round(self.trader.paper_mark_value(p, t), 4) if hasattr(self.trader, "paper_mark_value") and t else None,
                "mult": round(mult, 3),
                "tp_done": p.tp_done,
                "age_min": round((now - p.opened_at) / 60, 1),
                "curve_pct": round(t.curve_pct, 1) if t else None,
                "mcap_sol": round(t.mcap_sol, 1) if t else None,
            })

        watching = sorted([
            t for t in toks.values()
            if t.status in ("WAIT", "QUIET", "NEW") and getattr(t, "trades", 0) > 3
        ], key=lambda t: getattr(t, "curve_pct", 0), reverse=True)[:25]

        total_pnl = sum(pnls)
        safe_start = start if start != 0 else 1.0

        return {
            "now": now,
            "uptime_min": round((now - self.started) / 60, 1),
            "paused": self.paused,
            "account": {
                "equity": round(getattr(account, "equity", 0), 4),
                "cash": round(getattr(account, "cash", 0), 4),
                "starting_sol": start,
                "pnl": round(total_pnl, 4),
                "pnl_pct": round((total_pnl / safe_start) * 100, 2),
                "trades": len(pnls),
                "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else None,
                "avg_win": round(sum(wins) / len(wins), 4) if wins else None,
                "avg_loss": round(sum(p for p in pnls if p <= 0) / max(1, len(pnls) - len(wins)), 4) if len(pnls) > len(wins) else None,
            },
            "feed": {
                "events": self.stats["events"],
                "tracked": len(toks),
                "pruned": self.stats["pruned"],
            },
            "positions": positions,
            "watching": [{
                "mint": t.mint,
                "symbol": t.symbol,
                "curve_pct": round(getattr(t, "curve_pct", 0), 1),
                "top10_pct": round(getattr(t, "top10_pct", 0), 1),
                "dev_pct": round(getattr(t, "dev_pct", 0), 2),
                "dev_burned": getattr(t, "dev_burned", False),
                "buyers": len(getattr(t, "buyers", [])),
                "age_min": round((now - getattr(t, "born_at", now)) / 60, 1),
                "fail_reason": getattr(t, "reasons", [""])[0] if getattr(t, "reasons", None) else "",
            } for t in watching],
            "decisions": list(self.decisions),
            "reject_reasons": dict(self.fail_reasons.most_common(10)),
        }
