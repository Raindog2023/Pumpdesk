from pumpdesk.rules import Rules, load_rules, parse_rules, DEFAULT_YAML
from pumpdesk.filters import Facts, evaluate


def facts(**kw):
    d = dict(curve_pct=40, top10_pct=20, dev_pct=2, dev_sold_early=False,
             block0_buyers=1, age_min=5, local_hour=14)
    d.update(kw)
    return Facts(**d)


R = parse_rules(DEFAULT_YAML)


def test_default_yaml_has_article_rules():
    assert R.entry.max_top10_pct == 25 and R.entry.max_dev_pct == 5
    assert R.entry.max_block0_buyers == 3
    assert (R.entry.curve_min_pct, R.entry.curve_max_pct) == (15, 80)
    assert R.exit.take_profit_multiple == 2 and R.exit.take_profit_sell_pct == 50
    assert R.exit.stop_loss_pct == 35
    assert R.account.risk_per_trade_pct == 2 and R.account.max_open_positions == 5
    assert R.entry.quiet_hours == [1, 6]


def test_partial_yaml_falls_back_to_defaults():
    r = parse_rules("entry:\n  max_dev_pct: 3\n")
    assert r.entry.max_dev_pct == 3 and r.entry.max_top10_pct == 25


def test_invalid_yaml_raises_valueerror():
    import pytest
    with pytest.raises(ValueError):
        parse_rules("entry:\n  curve_min_pct: 90\n  curve_max_pct: 10\n")
    with pytest.raises(ValueError):
        parse_rules("entry: [unclosed")


def test_load_rules_creates_file_when_missing(tmp_path):
    p = tmp_path / "rules.yaml"
    r = load_rules(p)
    assert p.exists() and isinstance(r, Rules)


def test_pass(): assert evaluate(facts(), R).status == "PASS"
def test_top10_exactly_25_is_allowed(): assert evaluate(facts(top10_pct=25), R).status == "PASS"
def test_top10_over_waits_not_final(): assert evaluate(facts(top10_pct=25.1), R).status == "WAIT"
def test_dev_over_waits(): assert evaluate(facts(dev_pct=5.1), R).status == "WAIT"
def test_dev_exactly_5_ok(): assert evaluate(facts(dev_pct=5), R).status == "PASS"
def test_dev_sold_early_is_final_fail(): assert evaluate(facts(dev_sold_early=True), R).status == "FAIL"
def test_block0_four_is_final_fail(): assert evaluate(facts(block0_buyers=4), R).status == "FAIL"
def test_block0_three_ok(): assert evaluate(facts(block0_buyers=3), R).status == "PASS"
def test_early_curve_waits(): assert evaluate(facts(curve_pct=5), R).status == "WAIT"
def test_late_curve_expired(): assert evaluate(facts(curve_pct=85), R).status == "EXPIRED"
def test_too_old_expired(): assert evaluate(facts(age_min=400), R).status == "EXPIRED"
def test_hard_fail_beats_wait(): assert evaluate(facts(curve_pct=2, block0_buyers=9), R).status == "FAIL"
def test_quiet_hours_block_entry(): assert evaluate(facts(local_hour=3), R).status == "QUIET"
def test_quiet_hours_end_is_exclusive(): assert evaluate(facts(local_hour=6), R).status == "PASS"


def test_quiet_hours_wrapping_midnight():
    r = parse_rules("entry:\n  quiet_hours: [23, 2]\n")
    assert evaluate(facts(local_hour=0), r).status == "QUIET"
    assert evaluate(facts(local_hour=22), r).status == "PASS"


def test_all_reasons_listed():
    d = evaluate(facts(dev_sold_early=True, block0_buyers=5), R)
    assert len(d.reasons) == 2
