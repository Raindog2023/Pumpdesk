import pytest
from pumpdesk.tracker import Tracker

SUPPLY = 1_000_000_000 * 10**6
M = "MintAAA"


def create(slot=100):
    return {"kind": "create", "mint": M, "symbol": "TST", "name": "Test", "uri": "",
            "bonding_curve": "C", "user": "DEV", "creator": "DEV"}


def trade(user, tokens, buy=True, rtok=700_000_000_000_000, vsol=40 * 10**9, vtok=900 * 10**12, sol=10**8):
    return {"kind": "trade", "mint": M, "sol": sol, "tokens": tokens, "is_buy": buy, "user": user,
            "ts": 0, "vsol": vsol, "vtok": vtok, "rsol": 0, "rtok": rtok,
            "fee_bps": 95, "creator": "DEV", "creator_fee_bps": 30}


def pct(x): return int(SUPPLY * x / 100)


@pytest.fixture
def t():
    tr = Tracker()
    tr.on_event(create(), slot=100, now=1000.0)
    return tr


def test_unknown_mint_trades_are_ignored():
    tr = Tracker()
    assert tr.on_event(trade("X", 5), slot=1, now=1.0) is None
    assert M not in tr.tokens


def test_dev_initial_buy_counts_as_dev_not_bundle(t):
    t.on_event(trade("DEV", pct(3)), slot=100, now=1000.1)
    tok = t.tokens[M]
    assert tok.dev_pct == pytest.approx(3)
    assert tok.block0_buyers == 0


def test_launch_slot_buyers_are_counted_once_each(t):
    for u in ["A", "B", "A"]:
        t.on_event(trade(u, pct(1)), slot=100, now=1000.2)
    t.on_event(trade("C", pct(1)), slot=101, now=1000.6)
    assert t.tokens[M].block0_buyers == 2


def test_dev_sell_inside_window_is_flagged(t):
    t.on_event(trade("DEV", pct(3)), slot=100, now=1000)
    t.on_event(trade("DEV", pct(1), buy=False), slot=200, now=1000 + 9 * 60)
    tok = t.tokens[M]
    assert tok.dev_sold_at_min == pytest.approx(9)
    assert tok.dev_pct == pytest.approx(2)


def test_top10_pct_of_total_supply(t):
    for i in range(12):
        t.on_event(trade(f"U{i}", pct(i + 1)), slot=150, now=1001)
    # top 10 are 3..12 -> sum 75
    assert t.tokens[M].top10_pct() == pytest.approx(75)
    t.on_event(trade("U11", pct(12), buy=False), slot=151, now=1002)
    # U11 gone -> top 10 = 2..11 = 65
    assert t.tokens[M].top10_pct() == pytest.approx(65)


def test_curve_and_reserves_follow_latest_trade(t):
    t.on_event(trade("A", 1, rtok=396_550_000_000_000, vsol=55 * 10**9), slot=101, now=1001)
    tok = t.tokens[M]
    assert tok.curve_pct == pytest.approx(50)
    assert tok.vsol == 55 * 10**9 and tok.fee_bps == 125


def test_complete_marks_graduated(t):
    t.on_event({"kind": "complete", "mint": M}, slot=300, now=1100)
    assert t.tokens[M].graduated


def test_facts_reflect_state(t):
    t.on_event(trade("DEV", pct(2)), slot=100, now=1000)
    t.on_event(trade("DEV", pct(1), buy=False), slot=200, now=1000 + 60)
    f = t.tokens[M].facts(now=1000 + 120, local_hour=14, dev_window_min=10)
    assert f.dev_sold_early and f.age_min == pytest.approx(2) and f.dev_pct == pytest.approx(1)


def test_prune_drops_idle_tokens_but_keeps_protected(t):
    tr = t
    tr.on_event(create() | {"mint": "KEEP"}, slot=1, now=1000)
    removed = tr.prune(now=1000 + 3600, idle_s=600, keep={"KEEP"})
    assert removed == 1 and M not in tr.tokens and "KEEP" in tr.tokens


def test_nonstandard_curve_trade_flags_token(t):
    t.on_event(trade("A", 1, vsol=1_114_296_767, vtok=934_725_044_839_320, rtok=654_825_044_839_320), slot=101, now=1001)
    assert t.tokens[M].nonstandard
    f = t.tokens[M].facts(now=1002, local_hour=12, dev_window_min=10)
    assert f.nonstandard_curve


def test_standard_trade_keeps_token_standard(t):
    t.on_event(trade("A", 1, vsol=30 * 10**9, vtok=1_073_000_000 * 10**6, rtok=793_100_000_000_000), slot=101, now=1001)
    assert not t.tokens[M].nonstandard


def test_early_graduation_marks_token_nonstandard(t):
    t.on_event(trade("A", 1, vsol=30 * 10**9, vtok=1_073_000_000 * 10**6, rtok=793_100_000_000_000), slot=101, now=1001)
    t.on_event({"kind": "complete", "mint": M}, slot=102, now=1002)
    assert t.tokens[M].graduated and t.tokens[M].nonstandard


def test_normal_graduation_stays_standard(t):
    t.on_event(trade("A", 1, vsol=115_005_359_056, vtok=279_900_000 * 10**6, rtok=0), slot=101, now=1001)
    t.on_event({"kind": "complete", "mint": M}, slot=102, now=1002)
    assert t.tokens[M].graduated and not t.tokens[M].nonstandard
