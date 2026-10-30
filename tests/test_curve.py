import json
import pathlib

import pytest

from pumpdesk import curve
from pumpdesk.decoder import decode_program_data

S = json.loads((pathlib.Path(__file__).parent / "fixtures" / "samples.json").read_text())


def test_progress_zero_at_launch_and_100_when_sold_out():
    assert curve.progress_pct(793_100_000_000_000) == 0
    assert curve.progress_pct(0) == 100
    assert curve.progress_pct(396_550_000_000_000) == pytest.approx(50)


def test_buy_quote_reproduces_real_onchain_buy():
    ev = decode_program_data(S["buy"]["data"])
    pre_vsol, pre_vtok = ev["vsol"] - ev["sol"], ev["vtok"] + ev["tokens"]
    got = curve.tokens_out(pre_vsol, pre_vtok, ev["sol"])
    assert got == pytest.approx(ev["tokens"], rel=1e-6)


def test_sell_quote_reproduces_real_onchain_sell():
    ev = decode_program_data(S["sell"]["data"])
    pre_vsol, pre_vtok = ev["vsol"] + ev["sol"], ev["vtok"] - ev["tokens"]
    got = curve.sol_out(pre_vsol, pre_vtok, ev["tokens"])
    assert got == pytest.approx(ev["sol"], rel=1e-6)


def test_round_trip_costs_fees_both_ways():
    vsol, vtok = 30 * 10**9, 1_073_000_000 * 10**6
    budget = 10**9  # 1 SOL including fees
    toks = curve.buy_with_budget(vsol, vtok, budget, fee_bps=125)
    vsol2 = vsol + curve.net_of_buy_fee(budget, 125)
    vtok2 = vtok - toks
    back = curve.sell_net(vsol2, vtok2, toks, fee_bps=125)
    assert back == pytest.approx(budget * (1 - 0.0125) / 1.0125, rel=1e-3)


def test_price_is_vsol_over_vtok_in_sol_per_token():
    assert curve.price_sol(30 * 10**9, 1_073_000_000 * 10**6) == pytest.approx(30 / 1_073_000_000)


def test_standard_curve_invariant_detected_on_real_data():
    ev = decode_program_data(S["buy"]["data"])
    assert curve.is_standard(ev["vsol"], ev["vtok"], ev["rtok"])


def test_nonstandard_curves_rejected():
    # real examples from mainnet: drained / custom curves
    assert not curve.is_standard(0, 381_704_951_690_715, 101_804_951_690_715)
    assert not curve.is_standard(1_114_296_767, 934_725_044_839_320, 654_825_044_839_320)
    # standard launch state
    assert curve.is_standard(30 * 10**9, 1_073_000_000 * 10**6, 793_100_000_000_000)
