"""Tests for the rules optimizer (tools/optimize_rules.py)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Make tools importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from optimize_rules import (
    compute_metrics,
    entry_passes,
    merge_fixed,
    simulate_pnl,
    update_rules_yaml,
)

# ── entry_passes ──────────────────────────────────────────────────────

def test_entry_passes_accepts_meeting_all_criteria():
    t = {
        "entry_curve_pct": "20",
        "entry_top10_pct": "15",
        "entry_dev_pct": "3",
        "entry_block0": "2",
    }
    p = merge_fixed({"curve_min_pct": 15, "max_top10_pct": 25, "max_dev_pct": 5, "max_block0_buyers": 3})
    assert entry_passes(t, p) is True


def test_entry_passes_rejects_low_curve():
    t = {"entry_curve_pct": "10", "entry_top10_pct": "10", "entry_dev_pct": "0", "entry_block0": "1"}
    p = merge_fixed({"curve_min_pct": 15, "max_top10_pct": 25, "max_dev_pct": 5, "max_block0_buyers": 3})
    assert entry_passes(t, p) is False


def test_entry_passes_rejects_high_top10():
    t = {"entry_curve_pct": "20", "entry_top10_pct": "30", "entry_dev_pct": "0", "entry_block0": "1"}
    p = merge_fixed({"curve_min_pct": 15, "max_top10_pct": 25, "max_dev_pct": 5, "max_block0_buyers": 3})
    assert entry_passes(t, p) is False


def test_entry_passes_rejects_high_dev():
    t = {"entry_curve_pct": "20", "entry_top10_pct": "10", "entry_dev_pct": "10", "entry_block0": "1"}
    p = merge_fixed({"curve_min_pct": 15, "max_top10_pct": 25, "max_dev_pct": 5, "max_block0_buyers": 3})
    assert entry_passes(t, p) is False


def test_entry_passes_rejects_too_many_block0_buyers():
    t = {"entry_curve_pct": "20", "entry_top10_pct": "10", "entry_dev_pct": "0", "entry_block0": "5"}
    p = merge_fixed({"curve_min_pct": 15, "max_top10_pct": 25, "max_dev_pct": 5, "max_block0_buyers": 3})
    assert entry_passes(t, p) is False


def test_entry_passes_handles_missing_fields():
    t = {"entry_curve_pct": "20"}
    p = merge_fixed({"curve_min_pct": 15, "max_top10_pct": 25, "max_dev_pct": 5, "max_block0_buyers": 3})
    assert entry_passes(t, p) is True  # missing = 0, which passes


# ── simulate_pnl ──────────────────────────────────────────────────────

def test_simulate_pnl_stop_exit_uses_new_stop_loss():
    """A STOP exit should simulate at the new stop-loss level, not the old one."""
    t = {"cost_sol": "1.0", "pnl_pct": "-35", "peak_mult": "0.70", "exit_reason": "STOP"}
    p = merge_fixed({"stop_loss_pct": 15})
    assert simulate_pnl(t, p) == -0.15  # -15% of 1.0 SOL


def test_simulate_pnl_tp_hit_full_sell():
    """When peak_mult >= tp_multiple and full TP sell, P&L = cost * (tp - 1)."""
    t = {"cost_sol": "1.0", "pnl_pct": "50", "peak_mult": "2.5", "exit_reason": "TP"}
    p = merge_fixed({"take_profit_multiple": 1.5, "take_profit_sell_pct": 100})
    # 100% sold at 1.5x → 1.0 * (1.5 - 1) = 0.5
    assert simulate_pnl(t, p) == 0.5


def test_simulate_pnl_tp_hit_partial_sell():
    """With 50% TP sell, half at TP and half at actual avg exit."""
    t = {"cost_sol": "1.0", "pnl_pct": "50", "peak_mult": "2.5", "exit_reason": "TP"}
    p = merge_fixed({"take_profit_multiple": 1.5, "take_profit_sell_pct": 50})
    # 50% at (1.5-1)*1.0 = 0.25, 50% at actual 50% → 0.25 + 0.50*0.50 = 0.25 + 0.25 = 0.5
    # But actual_pnl_pct=50 includes the old TP sell, so remaining proxy uses 50% too
    assert abs(simulate_pnl(t, p) - 0.5) < 0.001


def test_simulate_pnl_non_tp_non_stop_uses_actual():
    """STALE/TIME exits should use the actual historical P&L."""
    t = {"cost_sol": "1.0", "pnl_pct": "-10", "peak_mult": "0.95", "exit_reason": "STALE"}
    p = merge_fixed({"stop_loss_pct": 15, "take_profit_multiple": 1.2, "take_profit_sell_pct": 100})
    assert simulate_pnl(t, p) == -0.10


def test_simulate_pnl_zero_cost():
    t = {"cost_sol": "0", "pnl_pct": "50", "peak_mult": "2.0", "exit_reason": "TP"}
    p = merge_fixed({"take_profit_multiple": 1.5, "take_profit_sell_pct": 100})
    assert simulate_pnl(t, p) == 0.0


def test_simulate_pnl_tp_not_hit_no_stop():
    """peak_mult < tp_multiple and exit not STOP → use actual P&L."""
    t = {"cost_sol": "1.0", "pnl_pct": "20", "peak_mult": "1.1", "exit_reason": "STALE"}
    p = merge_fixed({"stop_loss_pct": 15, "take_profit_multiple": 1.5, "take_profit_sell_pct": 100})
    assert simulate_pnl(t, p) == 0.20


# ── compute_metrics ───────────────────────────────────────────────────

def test_compute_metrics_basic():
    trades = [
        {"cost_sol": "1.0", "pnl_pct": "50", "peak_mult": "2.0", "exit_reason": "TP"},
        {"cost_sol": "1.0", "pnl_pct": "-10", "peak_mult": "0.95", "exit_reason": "STALE"},
        {"cost_sol": "1.0", "pnl_pct": "-15", "peak_mult": "0.80", "exit_reason": "STOP"},
        {"cost_sol": "1.0", "pnl_pct": "-15", "peak_mult": "0.80", "exit_reason": "STOP"},
    ]
    p = merge_fixed({"stop_loss_pct": 15, "take_profit_multiple": 1.2, "take_profit_sell_pct": 100})
    m = compute_metrics(trades, p)
    assert m is not None
    assert m["n"] == 4
    # TP trade: 1.0 * (1.2-1) * 1.0 = 0.20 (full sell at 1.2x) — wait, peak_mult=2.0 >= 1.2, so TP hit
    # Actually peak_mult=2.0 >= 1.2 → TP hit, 100% sell at 1.2x → 0.20
    # STALE: cost=1.0, pnl_pct=-10 → -0.10
    # STOP x2: cost=1.0, stop=15% → -0.15 each
    # Total: 0.20 - 0.10 - 0.15 - 0.15 = -0.20
    assert abs(m["total"] - (-0.20)) < 0.001
    assert m["wr"] == 25.0  # 1 win out of 4


def test_compute_metrics_empty():
    assert compute_metrics([], merge_fixed({})) is None


def test_compute_metrics_single_trade():
    trades = [{"cost_sol": "1.0", "pnl_pct": "100", "peak_mult": "2.0", "exit_reason": "TP"}]
    p = merge_fixed({"take_profit_multiple": 1.2, "take_profit_sell_pct": 100})
    m = compute_metrics(trades, p)
    assert m is not None
    assert m["n"] == 1
    assert m["sharpe"] == 0  # std=0, n=1 → sharpe=0


# ── update_rules_yaml ─────────────────────────────────────────────────

def test_update_rules_yaml_preserves_comments():
    original = """\
entry:
  curve_min_pct: 15           # wait until curve is full
  max_top10_pct: 25.0         # max top 10 ownership
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(original)
        f.flush()
        path = Path(f.name)

    try:
        update_rules_yaml(path, {"curve_min_pct": 30, "max_top10_pct": 15.0})
        content = path.read_text()
        assert "curve_min_pct: 30  # wait until curve is full" in content
        assert "max_top10_pct: 15.0  # max top 10 ownership" in content
    finally:
        path.unlink()


def test_update_rules_yaml_skips_commented_lines():
    original = """\
# curve_min_pct: 15  # this is a comment
  curve_min_pct: 20
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(original)
        f.flush()
        path = Path(f.name)

    try:
        update_rules_yaml(path, {"curve_min_pct": 30})
        content = path.read_text()
        assert "# curve_min_pct: 15  # this is a comment" in content  # unchanged
        assert "curve_min_pct: 30" in content  # updated
    finally:
        path.unlink()


# ── merge_fixed ───────────────────────────────────────────────────────

def test_merge_fixed_provides_exit_defaults():
    p = merge_fixed({"curve_min_pct": 20, "max_top10_pct": 25})
    assert p["stop_loss_pct"] == 35
    assert p["take_profit_multiple"] == 2.0
    assert p["take_profit_sell_pct"] == 50


def test_merge_fixed_overrides_defaults():
    p = merge_fixed({"stop_loss_pct": 15, "take_profit_multiple": 1.2})
    assert p["stop_loss_pct"] == 15
    assert p["take_profit_multiple"] == 1.2
