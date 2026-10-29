"""Glue: tracker -> filters -> paper engine -> journal/alerts. No network here."""
from __future__ import annotations

import csv
import json
import time
from collections import Counter, deque
from datetime import datetime
from pathlib import Path

from .filters import evaluate
from .paper import Paper
from .rules import Rules
from .tracker import Tracker

FINAL = {"PASS", "FAIL", "EXPIRED"}
TOKEN_FIELDS = ["time", "mint", "symbol", "status", "reasons", "curve_pct", "mcap_sol",
                "top10_pct", "dev_pct", "block0", "buyers", "age_min"]
TRADE_FIELDS = ["opened", "closed", "open_hour", "mint", "symbol", "hold_min", "cost_sol",
                "proceeds_sol", "pnl_sol", "pnl_pct", "peak_mult", "exit_reason",
                "entry_curve_pct", "entry_top10_pct", "entry_dev_pct", "entry_block0",
                "entry_buyers", "entry_age_min", "entry_mcap_sol"]


def _append_csv(path: Path, fields: list, row: dict):
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerow(row)


def _ts(t: float) -> str:
    return datetime.fromtimestamp(t).isoformat(timespec="seconds")


class Engine:
    def __init__(self, rules: Rules, data_dir: Path):
        self.rules = rules
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.tracker = Tracker()
        self.paper = self._load_paper()
        self.closed = self._load_closed()
        self.paused = False
        self.decisions: deque = deque(maxlen=300)
        self.alerts: list[str] = []
        self.stats = Counter()
        self.fail_reasons = Counter()
        self.started = time.time()

    # ---------- persistence
    def _load_paper(self) -> Paper:
        p = self.dir / "state.json"
        if p.exists():
            try:
                return Paper.from_state(self.rules, json.loads(p.read_text()))
            except Exception:  # corrupt state -> start fresh, keep a copy
                p.rename(self.dir / f"state.bad.{int(time.time())}.json")
        return Paper(self.rules)

    def _load_closed(self) -> list:
        p = self.dir / "trades.csv"
        if not p.exists():
            return []
        with p.open(encoding="utf-8") as f:
            return list(csv.DictReader(f))[-5000:]

    def save(self):
        tmp = self.dir / "state.json.tmp"
        tmp.write_text(json.dumps(self.paper.to_state()))
        tmp.replace(self.dir / "state.json")

    def set_rules(self, rules: Rules):
        self.rules = rules
        self.paper.rules = rules

    def reset_account(self):
        self.paper = Paper(self.rules)
        self.save()
        if (self.dir / "trades.csv").exists():
            (self.dir / "trades.csv").rename(self.dir / f"trades.{datetime.now():%Y%m%d-%H%M%S}.csv")
        self.closed = []

    # ---------- event handling
    def handle(self, slot: int, ev: dict, now: float, local_hour: int):
        self.stats["events"] += 1
        self.stats[ev["kind"]] += 1
        tok = self.tracker.on_event(ev, slot, now)
        if tok is None:
            return
        if ev["kind"] == "trade" and tok.status not in FINAL:
            f = tok.facts(now, local_hour, self.rules.entry.dev_sell_window_min)
            d = evaluate(f, self.rules)
            if d.status != tok.status:
                tok.status, tok.reasons = d.status, d.reasons
                if d.status in FINAL or d.status == "QUIET":
                    self._log_decision(tok, f, now)
            if d.status == "PASS" and not self.paused:
                info = {"curve_pct": round(f.curve_pct, 1), "top10_pct": round(f.top10_pct, 1),
                        "dev_pct": round(f.dev_pct, 2), "block0": f.block0_buyers,
                        "buyers": len(tok.buyers), "age_min": round(f.age_min, 1),
                        "mcap_sol": round(tok.mcap_sol, 1)}
                eq = self.paper.equity(self.tracker.tokens)
                if not self.paper.request_entry(tok, now, eq, info):
                    self.stats["pass_skipped"] += 1
        if tok.mint in self.paper.positions or tok.mint in self.paper.pending:
            self.paper.on_update(tok, now)
        self._drain_paper()

    def tick(self, now: float):
        self.paper.tick(self.tracker.tokens, now)
        self._drain_paper()
        keep = set(self.paper.positions) | set(self.paper.pending)
        self.stats["pruned"] += self.tracker.prune(now, idle_s=15 * 60, keep=keep)

    def _log_decision(self, tok, f, now):
        if tok.status == "FAIL":
            for r in tok.reasons:
                self.fail_reasons[r.split(":")[0].split(" ")[0]] += 1
        self.stats[f"status_{tok.status}"] += 1
        row = {"time": _ts(now), "mint": tok.mint, "symbol": tok.symbol, "status": tok.status,
               "reasons": "; ".join(tok.reasons), "curve_pct": round(f.curve_pct, 1),
               "mcap_sol": round(tok.mcap_sol, 1), "top10_pct": round(f.top10_pct, 1),
               "dev_pct": round(f.dev_pct, 2), "block0": f.block0_buyers,
               "buyers": len(tok.buyers), "age_min": round(f.age_min, 1)}
        self.decisions.appendleft(row)
        _append_csv(self.dir / "tokens.csv", TOKEN_FIELDS, row)

    def _drain_paper(self):
        if not self.paper.events and len(self.paper.closed) == 0:
            return
        al = self.rules.alerts
        for e in self.paper.events:
            link = f"https://pump.fun/coin/{e['mint']}"
            if e["type"] == "entry" and al.on_entry:
                self.alerts.append(f"🟢 PAPER BUY {e['symbol']}\n{e['sol']:.3f} SOL @ mcap {e['mcap_sol']:.0f} SOL, "
                                   f"curve {e['curve_pct']:.0f}%\nCA: {e['mint']}\n{link}")
            elif e["type"] == "exit" and al.on_exit:
                tag = "partial " if e["partial"] else ""
                self.alerts.append(f"🔴 PAPER {tag}SELL {e['symbol']} ({e['reason']})\n{e['sol']:.3f} SOL back\n{link}")
        self.paper.events.clear()
        for c in self.paper.closed:
            row = {**c, "opened": _ts(c["opened_at"]), "closed": _ts(c["closed_at"]),
                   "open_hour": datetime.fromtimestamp(c["opened_at"]).hour}
            _append_csv(self.dir / "trades.csv", TRADE_FIELDS, row)
            self.closed.append({k: row.get(k) for k in TRADE_FIELDS})
            if self.rules.alerts.on_exit:
                self.alerts.append(f"📒 CLOSED {c['symbol']}: {c['pnl_sol']:+.4f} SOL ({c['pnl_pct']:+.1f}%) "
                                   f"via {c['exit_reason']}, held {c['hold_min']:.1f}m")
        self.paper.closed.clear()

    def drain_alerts(self) -> list[str]:
        out, self.alerts = self.alerts, []
        return out

    # ---------- dashboard view
    def snapshot(self, now: float) -> dict:
        toks = self.tracker.tokens
        eq = self.paper.equity(toks)
        start = self.rules.account.starting_sol
        pnls = [float(c["pnl_sol"]) for c in self.closed]
        wins = [p for p in pnls if p > 0]
        by_hour: dict = {}
        for c in self.closed:
            h = int(c["open_hour"])
            b = by_hour.setdefault(h, {"hour": h, "trades": 0, "wins": 0, "pnl": 0.0})
            b["trades"] += 1; b["wins"] += float(c["pnl_sol"]) > 0; b["pnl"] += float(c["pnl_sol"])
        by_exit = Counter(str(c["exit_reason"]) for c in self.closed)
        positions = []
        for m, p in self.paper.positions.items():
            t = toks.get(m)
            mult = self.paper.mark_mult(p, t) if t else 1.0
            positions.append({"mint": m, "symbol": p.symbol, "cost": round(p.cost_sol, 4),
                              "value": round(self.paper.mark_value(p, t), 4) if t else None,
                              "mult": round(mult, 3), "tp_done": p.tp_done,
                              "age_min": round((now - p.opened_at) / 60, 1),
                              "curve_pct": round(t.curve_pct, 1) if t else None,
                              "mcap_sol": round(t.mcap_sol, 1) if t else None})
        watching = sorted((t for t in toks.values() if t.status in ("WAIT", "QUIET", "NEW") and t.trades > 3),
                          key=lambda t: -t.curve_pct)[:25]
        return {
            "now": now, "uptime_min": round((now - self.started) / 60, 1), "paused": self.paused,
            "account": {"equity": round(eq, 4), "cash": round(self.paper.cash, 4), "start": start,
                        "pnl": round(eq - start, 4), "pnl_pct": round((eq / start - 1) * 100, 2),
                        "trades": len(pnls), "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else None,
                        "avg_win": round(sum(wins) / len(wins), 4) if wins else None,
                        "avg_loss": round(sum(p for p in pnls if p <= 0) / max(1, len(pnls) - len(wins)), 4) if pnls else None,
                        "realized": round(sum(pnls), 4)},
            "feed": {"events": self.stats["events"], "creates": self.stats["create"],
                     "trades": self.stats["trade"], "tracked": len(toks),
                     "pass": self.stats["status_PASS"], "fail": self.stats["status_FAIL"],
                     "expired": self.stats["status_EXPIRED"], "pass_skipped": self.stats["pass_skipped"]},
            "positions": positions,
            "pending": [{"mint": o.mint, "side": o.side, "reason": o.reason} for o in self.paper.pending.values()],
            "tokens": list(self.decisions)[:100],
            "watching": [{"mint": t.mint, "symbol": t.symbol, "curve_pct": round(t.curve_pct, 1),
                          "top10_pct": round(t.top10_pct(), 1), "dev_pct": round(t.dev_pct, 2),
                          "block0": t.block0_buyers, "buyers": len(t.buyers), "status": t.status,
                          "reasons": "; ".join(t.reasons), "age_min": round((now - t.born) / 60, 1),
                          "mcap_sol": round(t.mcap_sol, 1)} for t in watching],
            "closed": self.closed[-50:][::-1],
            "by_hour": sorted(by_hour.values(), key=lambda b: b["hour"]),
            "by_exit": dict(by_exit),
            "fail_reasons": dict(self.fail_reasons.most_common(8)),
        }
