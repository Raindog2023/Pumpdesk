import pytest

from pumpdesk.paper import Paper
from pumpdesk.rules import DEFAULT_YAML, parse_rules
from pumpdesk.tracker import Token

R = parse_rules(DEFAULT_YAML)  # 5 SOL, 2% risk, TP 50% @2x, SL 35%, latency 600ms, 1% slip, 0.0005 tx


def tok(mint="M", vsol=40 * 10**9, vtok=800 * 10**12, last_trade=0.0):
    t = Token(mint, "TST", "Test", "DEV", 1, 0.0)
    t.vsol, t.vtok, t.fee_bps, t.last_trade = vsol, vtok, 125, last_trade
    return t


def price_mult(t: Token, m: float):
    """Move the curve so price is m x current (constant product)."""
    k = t.vsol * t.vtok
    # price = vsol/vtok ; vsol*vtok = k -> vsol = sqrt(k*p)
    import math
    p = t.vsol / t.vtok * m
    t.vsol = int(math.sqrt(k * p)); t.vtok = int(k / t.vsol)


def opened(p: Paper, t: Token, now=10.0):
    assert p.request_entry(t, now=now, equity=5.0, info={})
    p.on_update(t, now=now + 1)  # past latency -> fills
    return p.positions[t.mint]


def test_entry_waits_for_latency_before_filling():
    p, t = Paper(R), tok()
    assert p.request_entry(t, now=10.0, equity=5.0, info={})
    p.on_update(t, now=10.3)
    assert t.mint not in p.positions
    p.on_update(t, now=10.7)
    assert t.mint in p.positions


def test_entry_size_is_risk_pct_of_equity_and_costs_counted():
    p, t = Paper(R), tok()
    pos = opened(p, t)
    assert p.cash == pytest.approx(5.0 - 0.1)
    assert pos.cost_sol == pytest.approx(0.1)
    # immediate exit value is below cost by fees+slippage+tx costs (~4.5%)
    assert 0.94 < p.mark_mult(pos, t) < 0.97


def test_no_double_entry_and_max_positions():
    p = Paper(R)
    t = tok("A")
    opened(p, t)
    assert not p.request_entry(t, now=20, equity=5, info={})
    for i in range(4):
        opened(p, tok(f"B{i}"))
    assert not p.request_entry(tok("Z"), now=30, equity=5, info={})


def test_stop_loss_closes_after_latency_at_later_price():
    p, t = Paper(R), tok()
    opened(p, t)
    price_mult(t, 0.6)                # price drops below stop-loss threshold
    p.on_update(t, now=20.0)          # trigger -> pending sell
    assert t.mint in p.positions
    price_mult(t, 0.8)                # it dumps further before we land
    p.on_update(t, now=21.0)
    assert t.mint not in p.positions
    c = p.closed[-1]
    assert c["exit_reason"] == "STOP"
    assert c["pnl_pct"] < -50


def test_take_profit_sells_half_then_breakeven_stop():
    p, t = Paper(R), tok()
    pos = opened(p, t)
    toks0 = pos.tokens
    price_mult(t, 2.3)
    p.on_update(t, now=20.0); p.on_update(t, now=21.0)
    pos = p.positions[t.mint]
    assert pos.tp_done and pos.tokens == pytest.approx(toks0 / 2, rel=1e-6)
    price_mult(t, 1 / 2.4)            # back below entry
    p.on_update(t, now=30.0); p.on_update(t, now=31.0)
    assert t.mint not in p.positions
    c = p.closed[-1]
    assert c["exit_reason"] == "TP+BREAKEVEN"
    assert c["pnl_sol"] > 0


def test_time_and_stale_exits_from_tick():
    p, t = Paper(R), tok(last_trade=10.0)
    opened(p, t)
    p.tick({t.mint: t}, now=10 + 11 * 60)   # stale after 10 min without trades
    p.tick({t.mint: t}, now=10 + 11 * 60 + 1)
    assert p.closed[-1]["exit_reason"] == "STALE"


def test_graduation_closes_immediately():
    p, t = Paper(R), tok()
    opened(p, t)
    t.graduated = True
    p.on_update(t, now=50)
    assert p.closed[-1]["exit_reason"] == "GRADUATED"


def test_pending_entry_fills_via_tick_when_no_trades_arrive():
    p, t = Paper(R), tok()
    p.request_entry(t, now=10.0, equity=5.0, info={})
    p.tick({t.mint: t}, now=11.0)
    assert t.mint in p.positions


def test_cash_never_goes_negative():
    p = Paper(parse_rules("account:\n  starting_sol: 0.05\n  risk_per_trade_pct: 100\n"))
    assert not p.request_entry(tok(), now=1, equity=0.05, info={}) or p.cash >= 0


def test_state_roundtrip():
    p, t = Paper(R), tok()
    opened(p, t)
    q = Paper.from_state(R, p.to_state())
    assert q.cash == p.cash and set(q.positions) == set(p.positions)


def test_manual_close_request():
    p, t = Paper(R), tok()
    opened(p, t)
    assert p.request_exit(t.mint, now=40.0)
    p.on_update(t, now=41.0)
    assert p.closed[-1]["exit_reason"] == "MANUAL"
    assert not p.request_exit("nope", now=42.0)


def test_orphaned_position_closed_at_cost_basis_on_tick():
    """After an engine restart, positions whose tokens aren't in the tracker
    should be closed at cost basis (break-even) so position slots free up."""
    p, t = Paper(R), tok()
    opened(p, t)
    # Simulate token missing from tracker on next tick
    p.tick({}, now=100.0)  # places STALE sell order at cost basis
    p.tick({}, now=100.0)  # fills the sell order
    # Position should be closed
    assert t.mint not in p.positions
    c = p.closed[-1]
    assert c["exit_reason"] == "STALE"
    assert c["pnl_sol"] == pytest.approx(0.0, abs=0.001)
    assert c["symbol"] == "TST"
