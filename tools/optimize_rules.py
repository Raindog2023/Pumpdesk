"""Backtest optimizer for PumpDesk trading rules.

Reads data/trades.csv (historical paper trades with entry facts, peak_mult,
and actual P&L), sweeps entry + exit rule parameter combinations, and ranks
them by Sharpe ratio on a holdout validation set (walk-forward: train on
earlier trades, validate on later ones).

Entry selection uses trade entry facts (curve, top10, dev, block0).
Exit P&L is *approximated* from peak_mult and exit_reason because we don't
have the full price path — see simulate_pnl() docstring for the math.

Usage:
  python tools/optimize_rules.py                    # dry run, show top 20
  python tools/optimize_rules.py --top 5            # show top 5
  python tools/optimize_rules.py --apply             # write best to data/rules.yaml
  python tools/optimize_rules.py --apply --notify    # also Telegram-alert on change
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TRADES = ROOT / "data" / "trades.csv"


def load_trades(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print(f"❌ No trades in {path}. Paper trade first.", file=sys.stderr)
        sys.exit(1)
    return rows


def entry_passes(t: dict, p: dict) -> bool:
    """Check if a trade's entry facts satisfy the entry parameter set."""
    try:
        c = float(t.get("entry_curve_pct") or 0)
        t10 = float(t.get("entry_top10_pct") or 0)
        dev = float(t.get("entry_dev_pct") or 0)
        b0 = int(float(t.get("entry_block0", 0) or 0))
    except (ValueError, TypeError):
        return False
    return (
        c >= p["curve_min_pct"]
        and c <= p["curve_max_pct"]
        and t10 <= p["max_top10_pct"]
        and dev <= p["max_dev_pct"]
        and b0 <= p["max_block0_buyers"]
    )


def simulate_pnl(t: dict, p: dict) -> float:
    """Approximate the P&L (in SOL) for a single trade under the given exit
    parameters.

    We only have the trade's peak_mult and exit_reason, not the full price
    path, so this is a rough but directional approximation:

    • TP hit (peak_mult >= take_profit_multiple):
        take_profit_sell_pct% sold at TP level, the rest at the trade's
        historical average exit (approximated by the actual pnl_pct).
    • STOP (exit_reason == 'STOP', TP not hit):
        Re-exit at the new stop_loss_pct level instead of the old 35%.
    • Other exits (STALE, TIME, GRADUATED):
        P&L unchanged — exit rules don't affect these.
    """
    cost = float(t.get("cost_sol", 0)) or 0.0
    if cost == 0:
        return 0.0
    actual_pct = float(t.get("pnl_pct", 0)) or 0.0
    peak_mult = float(t.get("peak_mult", 1.0)) or 1.0
    exit_reason = t.get("exit_reason", "")

    tp_pct = p["take_profit_sell_pct"] / 100.0

    if peak_mult >= p["take_profit_multiple"]:
        # TP was hit: partial at new TP level, rest at historical avg exit
        tp_pnl = cost * (p["take_profit_multiple"] - 1) * tp_pct
        rest_pnl = cost * (actual_pct / 100.0) * (1 - tp_pct)
        return tp_pnl + rest_pnl

    if exit_reason == "STOP":
        # Stopped out at new stop-loss level
        return cost * (-p["stop_loss_pct"] / 100.0)

    # Non-TP, non-STOP exit: use actual historical P&L
    return cost * (actual_pct / 100.0)


def compute_metrics(trades: list[dict], p: dict) -> dict | None:
    """Compute performance metrics using simulated P&L."""
    if not trades:
        return None
    pnls = [simulate_pnl(t, p) for t in trades]
    n = len(pnls)
    total = sum(pnls)
    avg = total / n
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    var = sum((x - avg) ** 2 for x in pnls) / n if n > 1 else 0
    std = var ** 0.5
    sharpe = (avg / std) * (n ** 0.5) if std > 0 else 0.0
    # Path-wise max drawdown
    cum = 0.0
    peak = 0.0
    dd = 0.0
    for x in pnls:
        cum += x
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return {
        "n": n,
        "total": round(total, 4),
        "avg": round(avg, 6),
        "wr": round(len(wins) / n * 100, 1),
        "sharpe": round(sharpe, 2),
        "max_dd": round(dd, 4),
        "avg_win": round(sum(wins) / len(wins), 6) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses), 6) if losses else 0,
    }


def scan(trades: list[dict], grid: dict, min_trades: int) -> list[tuple[dict, dict]]:
    """Sweep all parameter combinations and return (params, metrics) pairs
    that meet the minimum trade-count threshold."""
    keys = list(grid)
    combos = list(product(*[grid[k] for k in keys]))
    out = []
    for vals in combos:
        p = dict(zip(keys, vals))
        p_full = merge_fixed(p)  # ensure all params present (entry + exit defaults)
        filtered = [t for t in trades if entry_passes(t, p_full)]
        m = compute_metrics(filtered, p_full)
        if m and m["n"] >= min_trades:
            out.append((p, m))
    return out


def update_rules_yaml(path: Path, updates: dict) -> None:
    """Update specific key:value pairs in rules.yaml, preserving comments."""
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        for key, value in updates.items():
            if re.match(rf"^\s*{key}\s*:", line):
                indent = line[: len(line) - len(line.lstrip())]
                comment = ""
                if "#" in line:
                    comment = "  " + line[line.index("#") :]
                lines[i] = f"{indent}{key}: {value}{comment}"
                break
    path.write_text("\n".join(lines) + "\n")


def telegram_notify(token: str, chat_id: str, msg: str) -> bool:
    import httpx

    r = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": msg, "disable_web_page_preview": True},
        timeout=10,
    )
    return r.status_code == 200


# ── search grids ──────────────────────────────────────────────────────────

ENTRY_GRID = {
    "curve_min_pct":    [15, 20, 25, 30],
    "curve_max_pct":    [70, 80, 90],
    "max_top10_pct":    [15, 20, 25, 30],
    "max_dev_pct":      [2, 3, 5, 7],
    "max_block0_buyers":[1, 2, 3, 4],
}

EXIT_GRID = {
    "stop_loss_pct":        [15, 20, 25, 30],
    "take_profit_multiple": [1.2, 1.5, 2.0, 3.0],
    "take_profit_sell_pct": [50, 100],
}

FIXED_PARAMS = {
    # entry defaults
    "curve_min_pct": 15,
    "curve_max_pct": 80,
    "max_top10_pct": 25.0,
    "max_dev_pct": 5.0,
    "max_block0_buyers": 3,
    "max_token_age_min": 360,
    "quiet_hours": [1, 6],
    "dev_sell_window_min": 10,
    # exit defaults
    "breakeven_after_tp": True,
    "max_hold_min": 60,
    "stale_exit_min": 10,
    "exit_on_graduation": True,
}


def build_grid(phase: str) -> dict:
    """Return the parameter grid for the given phase."""
    if phase == "entry":
        return dict(ENTRY_GRID)
    if phase == "exit":
        return dict(EXIT_GRID)
    if phase == "combined":
        return {**ENTRY_GRID, **EXIT_GRID}
    raise ValueError(f"Unknown phase: {phase}")


def merge_fixed(params: dict) -> dict:
    """Merge sweep params with fixed params so simulate_pnl has everything."""
    merged = dict(FIXED_PARAMS)
    merged.update(params)
    # Ensure exit defaults for entry-only phase
    if "stop_loss_pct" not in merged:
        merged["stop_loss_pct"] = 35
    if "take_profit_multiple" not in merged:
        merged["take_profit_multiple"] = 2.0
    if "take_profit_sell_pct" not in merged:
        merged["take_profit_sell_pct"] = 50
    return merged


def main():
    ap = argparse.ArgumentParser(description="Optimize PumpDesk trading rules (backtest)")
    ap.add_argument("--trades", default=str(DEFAULT_TRADES), help="path to trades.csv")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--apply", action="store_true", help="write best params to rules.yaml")
    ap.add_argument("--notify", action="store_true", help="Telegram-alert on apply")
    ap.add_argument("--min-trades", type=int, default=10)
    ap.add_argument("--min-val-trades", type=int, default=10,
                    help="minimum validation trades for a combo to be ranked (combined phase)")
    ap.add_argument("--val-split", type=float, default=0.7, help="fraction of trades for training")
    ap.add_argument("--no-val", action="store_true", help="skip walk-forward validation")
    ap.add_argument("--phase", choices=["entry", "exit", "combined"],
                    default="combined", help="which params to sweep")
    args = ap.parse_args()

    all_trades = load_trades(Path(args.trades))
    print(f"Loaded {len(all_trades)} trades from {args.trades}")

    grid = build_grid(args.phase)

    # Walk-forward split
    if args.no_val or not all_trades:
        train = all_trades
        val = []
        print(f"Train (all): {len(train)} trades")
    else:
        split = int(len(all_trades) * args.val_split)
        train, val = all_trades[:split], all_trades[split:]
        print(f"Train: {len(train)} | Val: {len(val)} (walk-forward split at {args.val_split})")

    n_combos = 1
    for v in grid.values():
        n_combos *= len(v)
    print(f"Testing {n_combos} {args.phase}-rule combinations (min {args.min_trades} trades)...")

    train_results = scan(train, grid, args.min_trades)
    print(f"  {len(train_results)} combos met min-trade threshold on train set")

    if val:
        val_min = max(3, args.min_trades // 3)
        val_results = scan(val, grid, val_min)
        val_map = {tuple(sorted(p.items())): vm for p, vm in val_results}
        ranked = []
        for p, tm in train_results:
            key = tuple(sorted(p.items()))
            vm = val_map.get(key)
            if vm:
                ranked.append((p, tm, vm))
        print(f"  {len(ranked)} combos also had enough validation trades")
    else:
        ranked = [(p, tm, tm) for p, tm in train_results]

    # Rank by validation Sharpe, then total return
    ranked.sort(key=lambda x: (x[2]["sharpe"], x[2]["total"]), reverse=True)

    # Filter out combos with too few validation trades (statistical noise)
    if val:
        before = len(ranked)
        ranked = [r for r in ranked if r[2]["n"] >= args.min_val_trades]
        if ranked:
            print(f"  {before} → {len(ranked)} after min_val_trades={args.min_val_trades} filter")
        else:
            print(f"  {before} → 0 after min_val_trades={args.min_val_trades} filter "
                  f"(try lowering --min-val-trades)")

    if not ranked:
        print("❌ No valid combinations found. Lower --min-trades or widen the grid.")
        sys.exit(1)

    # Print header
    print(f"\n{'Rank':<5} {'Val SH':<8} {'Val P&L':<10} {'Val WR':<6} {'Val DD':<8} "
          f"{'Tr n':<5} {'Params'}")
    print("-" * 110)

    for i, (p, tm, vm) in enumerate(ranked[: args.top]):
        flag = "⭐" if i == 0 else "  "
        param_str = " ".join(f"{k}:{v}" for k in sorted(p) for v in [p[k]])
        print(f"{flag}{i+1:<4} {vm['sharpe']:<8.2f} {vm['total']:>+9.3f} {vm['wr']:<5.0f}% {vm['max_dd']:<+8.3f} "
              f"{vm['n']:<5d} {param_str}")

    if args.apply:
        best_p, best_tm, best_vm = ranked[0]
        rules_path = ROOT / "data" / "rules.yaml"
        update_rules_yaml(rules_path, best_p)
        print(f"\n✅ Applied best parameters to {rules_path}")
        for k in sorted(best_p):
            print(f"  {k}: {best_p[k]}")

        if args.notify:
            token = os.environ.get("TG_TOKEN", "")
            chat_id = os.environ.get("TG_CHAT_ID", "")
            if token and chat_id:
                msg = (
                    "🎯 PumpDesk optimizer found better rules:\n\n"
                    f"Train: n={best_tm['n']} SH={best_tm['sharpe']:.2f} "
                    f"P&L={best_tm['total']:+.3f} WR={best_tm['wr']:.0f}%\n"
                    f"Val:   n={best_vm['n']} SH={best_vm['sharpe']:.2f} "
                    f"P&L={best_vm['total']:+.3f} WR={best_vm['wr']:.0f}%\n\n"
                    + "\n".join(f"{k}: {best_p[k]}" for k in sorted(best_p)) +
                    "\n\nApplied to rules.yaml. Review in dashboard → Rules tab."
                )
                if telegram_notify(token, chat_id, msg):
                    print("✅ Telegram alert sent")
                else:
                    print("❌ Telegram alert failed")
            else:
                print("⚠️  TG_TOKEN/TG_CHAT_ID not in environment — skip Telegram alert")

    if not args.apply:
        print(f"\n(Dry run. Add --apply to write best parameters to {ROOT / 'data' / 'rules.yaml'})")


if __name__ == "__main__":
    main()
