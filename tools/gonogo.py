"""PumpDesk go/no-go evaluator.

Implements the four readiness gates from README.md:

1. At least 100 closed trades.
2. Profitable after costs, and still profitable if the single best trade is removed.
3. Profitable in at least 3 of 4 separate weeks (judge the worst week).
4. Fewer than ~10 rule changes (each tweak is another trial).

Usage:
    python tools/gonogo.py [data_dir] [--json]

Exit code 0 when the desk is GO for live trading, 1 when it is NO-GO.
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

MIN_TRADES = 100
PROFITABLE_WEEKS = 3
WEEKS_JUDGED = 4
MAX_RULE_CHANGES = 10
ROOT = Path(__file__).resolve().parent.parent


def load_trades(data_dir: Path) -> list[dict]:
    p = data_dir / "trades.csv"
    if not p.exists():
        return []
    with p.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def week_key(ts: datetime) -> str:
    y, w, _ = ts.isocalendar()
    return f"{y}-W{w:02d}"


def parse_opened(row: dict) -> datetime | None:
    raw = (row.get("opened") or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def count_rule_changes(data_dir: Path) -> tuple[int, str]:
    """Rule-change count: prefer the live log the engine writes, else git history."""
    log = data_dir / "rules_log.jsonl"
    if log.exists():
        hashes = {ln.strip() for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()}
        return len(hashes), "rules_log.jsonl"
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "log", "--oneline", "--", "pumpdesk/rules.py", "data/rules.yaml"],
            capture_output=True, text=True, timeout=20).stdout.strip()
        return (len(out.splitlines()) if out else 0), "git history"
    except Exception:  # noqa: BLE001
        return 0, "unknown"


def evaluate(data_dir: Path) -> dict:
    rows = load_trades(data_dir)
    pnls: list[float] = []
    for r in rows:
        try:
            pnls.append(float(r.get("pnl_sol") or 0))
        except ValueError:
            continue

    total = sum(pnls)
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    best = max(pnls) if pnls else 0.0
    worst = min(pnls) if pnls else 0.0
    without_best = total - best
    without_worst = total - worst

    # weekly buckets
    weeks: dict[str, dict] = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    undated = 0
    for r, p in zip(rows, pnls):
        dt = parse_opened(r)
        if dt is None:
            undated += 1
            continue
        b = weeks[week_key(dt)]
        b["trades"] += 1
        b["pnl"] += p
        b["wins"] += 1 if p > 0 else 0
    ordered = sorted(weeks.items())
    # the last N calendar weeks that have data; judge the worst of them
    recent = ordered[-WEEKS_JUDGED:]
    profitable_weeks = sum(1 for _, b in recent if b["pnl"] > 0)
    weeks_with_data = len(recent)
    span_days = 0
    if len(ordered) >= 2:
        first = parse_opened(rows[0]) if rows else None
        last = parse_opened(rows[-1]) if rows else None
        if first and last:
            span_days = max(0, (last - first).days)
    elif ordered:
        span_days = 0

    rule_changes, rule_src = count_rule_changes(data_dir)

    c1 = n >= MIN_TRADES
    c2 = (total > 0) and (without_best > 0)
    c3 = (weeks_with_data >= WEEKS_JUDGED) and (profitable_weeks >= PROFITABLE_WEEKS)
    c4 = rule_changes < MAX_RULE_CHANGES

    gates = [
        {"id": 1, "name": "at least 100 closed trades", "ok": c1,
         "detail": f"{n} closed trades (need {MIN_TRADES})",
         "progress": f"{min(100, round(n / MIN_TRADES * 100))}%"},
        {"id": 2, "name": "profitable after costs, and without the best trade", "ok": c2,
         "detail": f"net {total:+.4f} SOL, without best {without_best:+.4f} SOL",
         "progress": "yes" if c2 else "no"},
        {"id": 3, "name": "profitable in at least 3 of 4 separate weeks", "ok": c3,
         "detail": f"{profitable_weeks}/{weeks_with_data} judged weeks profitable "
                   f"(need {PROFITABLE_WEEKS} of {WEEKS_JUDGED})",
         "progress": f"{profitable_weeks}/{weeks_with_data}"},
        {"id": 4, "name": "fewer than ~10 rule changes", "ok": c4,
         "detail": f"{rule_changes} rule changes counted via {rule_src} (limit {MAX_RULE_CHANGES})",
         "progress": f"{rule_changes}/{MAX_RULE_CHANGES}"},
    ]
    go = all(g["ok"] for g in gates)

    return {
        "verdict": "GO" if go else "NO-GO",
        "trades": n,
        "net_pnl_sol": round(total, 4),
        "without_best_sol": round(without_best, 4),
        "without_worst_sol": round(without_worst, 4),
        "win_rate_pct": round(len(wins) / n * 100, 1) if n else None,
        "avg_win_sol": round(sum(wins) / len(wins), 4) if wins else None,
        "avg_loss_sol": round((total - sum(wins)) / (n - len(wins)), 4) if n > len(wins) else None,
        "best_trade_sol": round(best, 4),
        "worst_trade_sol": round(worst, 4),
        "weeks": [{"week": k, **v, "pnl": round(v["pnl"], 4)} for k, v in ordered],
        "profitable_weeks": profitable_weeks,
        "weeks_judged": weeks_with_data,
        "rule_changes": rule_changes,
        "span_days": span_days,
        "undated_trades": undated,
        "gates": gates,
        "blocking": [g["name"] for g in gates if not g["ok"]],
    }


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    data_dir = Path(args[0]) if args else ROOT / "data"
    r = evaluate(data_dir)
    if "--json" in sys.argv:
        print(json.dumps(r, indent=2))
        return 0 if r["verdict"] == "GO" else 1

    print(f"PumpDesk readiness: {r['verdict']}")
    print(f"  trades={r['trades']}  net={r['net_pnl_sol']:+.4f} SOL  "
          f"win_rate={r['win_rate_pct']}%  span={r['span_days']}d")
    print()
    for g in r["gates"]:
        mark = "PASS" if g["ok"] else "FAIL"
        print(f"  [{mark}] {g['id']}. {g['name']}")
        print(f"         {g['detail']}")
    if r["weeks"]:
        print("\n  weekly:")
        for w in r["weeks"]:
            print(f"    {w['week']}: {w['trades']:>3} trades  {w['pnl']:+.4f} SOL  "
                  f"{w['wins']} wins")
    if r["blocking"]:
        print(f"\n  not ready because: {'; '.join(r['blocking'])}")
    else:
        print("\n  all gates pass - live trading is justified by the paper record.")
    return 0 if r["verdict"] == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
