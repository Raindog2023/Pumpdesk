"""Your trading rules: the preference memory. Lives in data/rules.yaml."""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path

import yaml

DEFAULT_YAML = """\
# PumpDesk rules: your preference memory.
# Edit here or in the dashboard's Rules tab; changes apply instantly.
# Defaults come from the TRADING DESK prompt pack.

account:
  starting_sol: 5.0           # paper account size
  risk_per_trade_pct: 2.0     # % of equity per position
  max_open_positions: 5

entry:
  max_top10_pct: 25.0         # skip if top 10 holders own MORE than this % of supply
  max_dev_pct: 5.0            # skip if dev holds more than this %
  dev_sell_window_min: 10     # reject forever if dev sells within N min of launch
  max_block0_buyers: 3        # reject forever if more wallets bought in the launch slot
  curve_min_pct: 15           # wait until the bonding curve is at least this full
  curve_max_pct: 80           # too late after this
  max_token_age_min: 360      # ignore tokens older than 6h
  quiet_hours: [1, 6]         # no new entries from 01:00 to 06:00 local time

exit:
  take_profit_multiple: 2.0   # at 2x...
  take_profit_sell_pct: 50    # ...sell this % of the position
  stop_loss_pct: 35           # cut at -35%
  breakeven_after_tp: true    # after the take-profit, stop the rest at entry
  max_hold_min: 60            # time stop
  stale_exit_min: 10          # exit if nobody trades it for N min
  exit_on_graduation: true    # close when the token graduates to PumpSwap

sim:
  latency_ms: 600             # your reaction + tx landing time; fills use prices AFTER this
  extra_slippage_bps: 100     # extra 1% haircut on every fill
  tx_cost_sol: 0.0005         # priority fee + base fee per transaction

alerts:
  telegram: true
  on_entry: true
  on_exit: true
"""


@dataclass
class Account:
    starting_sol: float = 5.0
    risk_per_trade_pct: float = 2.0
    max_open_positions: int = 5


@dataclass
class Entry:
    max_top10_pct: float = 25.0
    max_dev_pct: float = 5.0
    dev_sell_window_min: float = 10
    max_block0_buyers: int = 3
    curve_min_pct: float = 15
    curve_max_pct: float = 80
    max_token_age_min: float = 360
    quiet_hours: list = field(default_factory=lambda: [1, 6])


@dataclass
class Exit:
    take_profit_multiple: float = 2.0
    take_profit_sell_pct: float = 50
    stop_loss_pct: float = 35
    breakeven_after_tp: bool = True
    max_hold_min: float = 60
    stale_exit_min: float = 10
    exit_on_graduation: bool = True


@dataclass
class Sim:
    latency_ms: float = 600
    extra_slippage_bps: float = 100
    tx_cost_sol: float = 0.0005


@dataclass
class Alerts:
    telegram: bool = True
    on_entry: bool = True
    on_exit: bool = True


@dataclass
class Rules:
    account: Account = field(default_factory=Account)
    entry: Entry = field(default_factory=Entry)
    exit: Exit = field(default_factory=Exit)
    sim: Sim = field(default_factory=Sim)
    alerts: Alerts = field(default_factory=Alerts)


def _fill(obj, data: dict, path: str):
    if not isinstance(data, dict):
        raise ValueError(f"'{path}' must be a section of key: value lines")
    known = {f.name: f for f in fields(obj)}
    for k, v in data.items():
        if k not in known:
            raise ValueError(f"unknown setting '{path}.{k}'")
        cur = getattr(obj, k)
        if is_dataclass(cur):
            _fill(cur, v, f"{path}.{k}")
        elif isinstance(cur, bool):
            if not isinstance(v, bool):
                raise ValueError(f"'{path}.{k}' must be true or false")
            setattr(obj, k, v)
        elif isinstance(cur, (int, float)):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError(f"'{path}.{k}' must be a number")
            setattr(obj, k, type(cur)(v) if isinstance(cur, float) else v)
        elif isinstance(cur, list):
            if not (isinstance(v, list) and len(v) == 2 and all(isinstance(x, int) and 0 <= x <= 24 for x in v)):
                raise ValueError(f"'{path}.{k}' must look like [1, 6] (hours 0-24)")
            setattr(obj, k, v)


def parse_rules(text: str) -> Rules:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"YAML error: {e}") from e
    r = Rules()
    for k, v in data.items():
        if k not in {"account", "entry", "exit", "sim", "alerts"}:
            raise ValueError(f"unknown section '{k}'")
        _fill(getattr(r, k), v, k)
    e = r.entry
    if not 0 <= e.curve_min_pct < e.curve_max_pct <= 100:
        raise ValueError("curve_min_pct must be below curve_max_pct (0-100)")
    if not 0 < r.account.risk_per_trade_pct <= 100:
        raise ValueError("risk_per_trade_pct must be between 0 and 100")
    if not 0 < r.exit.stop_loss_pct < 100:
        raise ValueError("stop_loss_pct must be between 0 and 100")
    if r.exit.take_profit_multiple <= 1:
        raise ValueError("take_profit_multiple must be above 1")
    if r.account.max_open_positions < 1 or r.account.starting_sol <= 0:
        raise ValueError("max_open_positions must be >= 1 and starting_sol > 0")
    return r


def load_rules(path: Path) -> Rules:
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_YAML, encoding="utf-8")
    return parse_rules(path.read_text(encoding="utf-8"))
