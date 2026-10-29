"""Entry filter. Pure function of token facts + your rules."""
from __future__ import annotations

from dataclasses import dataclass, field

from .rules import Rules


@dataclass
class Facts:
    curve_pct: float
    top10_pct: float
    dev_pct: float
    dev_sold_early: bool
    block0_buyers: int
    age_min: float
    local_hour: int


@dataclass
class Decision:
    # PASS: enter. WAIT: re-check on next trade. QUIET: would pass, but quiet hours.
    # FAIL: rejected for good. EXPIRED: window missed.
    status: str
    reasons: list = field(default_factory=list)


def in_quiet_hours(hour: int, window: list) -> bool:
    start, end = window
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def evaluate(f: Facts, r: Rules) -> Decision:
    e = r.entry
    hard = []
    if f.dev_sold_early:
        hard.append(f"dev sold within {e.dev_sell_window_min:g}m of launch")
    if f.block0_buyers > e.max_block0_buyers:
        hard.append(f"bundle: {f.block0_buyers} launch-slot buyers > {e.max_block0_buyers}")
    if hard:
        return Decision("FAIL", hard)
    if f.curve_pct > e.curve_max_pct:
        return Decision("EXPIRED", [f"curve {f.curve_pct:.0f}% > {e.curve_max_pct:g}%"])
    if f.age_min > e.max_token_age_min:
        return Decision("EXPIRED", [f"older than {e.max_token_age_min:g}m"])
    soft = []
    if f.curve_pct < e.curve_min_pct:
        soft.append(f"curve {f.curve_pct:.0f}% < {e.curve_min_pct:g}%")
    if f.top10_pct > e.max_top10_pct:
        soft.append(f"top10 {f.top10_pct:.1f}% > {e.max_top10_pct:g}%")
    if f.dev_pct > e.max_dev_pct:
        soft.append(f"dev {f.dev_pct:.1f}% > {e.max_dev_pct:g}%")
    if soft:
        return Decision("WAIT", soft)
    if in_quiet_hours(f.local_hour, e.quiet_hours):
        return Decision("QUIET", [f"quiet hours {e.quiet_hours[0]:02d}:00-{e.quiet_hours[1]:02d}:00"])
    return Decision("PASS", [])
