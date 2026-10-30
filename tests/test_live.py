"""Tests for the live trading module — instruction construction, PDA derivation,
wallet loading, and LiveTrader behaviour with a mock RPC."""
from __future__ import annotations

import json
import math
import struct
import tempfile
import time
from pathlib import Path

import pytest

from pumpdesk.live import (
    BUY_DISC, SELL_DISC, FEE_CONFIG_CONST, GLOBAL_PDA_ADDR,
    PUMP_PROGRAM_ID, SYSTEM_PROGRAM, TOKEN_PROGRAM,
    Wallet, Rpc, LiveConfig, LiveTrader,
    _option_bool, _find_pda, _ata, _b58,
    build_buy_ix, build_sell_ix,
    _CREATOR_CACHE,
)
from pumpdesk.paper import Position, Order
from pumpdesk.rules import DEFAULT_YAML, parse_rules
from pumpdesk.tracker import Token

R = parse_rules(DEFAULT_YAML)

TEST_MINT = "5xon3RV9YDVoAn7wtK96VgnF5jwdJwkV6hX7So5nEaA"
TEST_USER = "11111111111111111111111111111112"
TEST_CREATOR = "11111111111111111111111111111113"
TEST_FEE_RECIPIENT = "62qc2CNXwrYqQScmEdiZFFAnJR262PxWEuNQtxfafNgV"


# ── OptionBool ───────────────────────────────────────────────────────────
def test_option_bool_encoding():
    assert _option_bool(None) == b"\x00"
    assert _option_bool(True) == b"\x01"
    assert _option_bool(False) == b"\x00"


# ── PDA derivations ─────────────────────────────────────────────────────
def test_global_pda_is_known():
    g, _ = _find_pda([b"global"], PUMP_PROGRAM_ID)
    assert str(g) == GLOBAL_PDA_ADDR


def test_bonding_curve_pda_deterministic():
    bc, _ = _find_pda([b"bonding-curve", _b58(TEST_MINT)], PUMP_PROGRAM_ID)
    bc2, _ = _find_pda([b"bonding-curve", _b58(TEST_MINT)], PUMP_PROGRAM_ID)
    assert str(bc) == str(bc2)
    assert len(bytes(bc)) == 32


def test_creator_vault_pda():
    cv, _ = _find_pda([b"creator-vault", _b58(TEST_CREATOR)], PUMP_PROGRAM_ID)
    assert len(bytes(cv)) == 32


def test_fee_config_pda():
    fc, _ = _find_pda([b"fee_config", FEE_CONFIG_CONST],
                      "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ")
    assert len(bytes(fc)) == 32


def test_event_authority_pda():
    ea, _ = _find_pda([b"__event_authority"], PUMP_PROGRAM_ID)
    assert len(bytes(ea)) == 32


def test_volume_accumulator_pdAs():
    gva, _ = _find_pda([b"global_volume_accumulator"], PUMP_PROGRAM_ID)
    uva, _ = _find_pda([b"user_volume_accumulator", _b58(TEST_USER)], PUMP_PROGRAM_ID)
    assert len(bytes(gva)) == 32
    assert len(bytes(uva)) == 32


# ── ATA ──────────────────────────────────────────────────────────────────
def test_ata_derivation():
    a1 = _ata(TEST_USER, TEST_MINT)
    a2 = _ata(TEST_USER, TEST_MINT)
    assert a1 == a2


# ── Wallet ──────────────────────────────────────────────────────────────
def test_wallet_from_file():
    from solders.keypair import Keypair
    kp = Keypair()
    arr = list(bytes(kp))
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump(arr, f)
        path = f.name
    try:
        w = Wallet.from_file(path)
        assert w.pubkey == str(kp.pubkey())
    finally:
        Path(path).unlink()


def test_wallet_rejects_short_key():
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump([1] * 32, f)
        path = f.name
    try:
        with pytest.raises(ValueError, match="64-byte"):
            Wallet.from_file(path)
    finally:
        Path(path).unlink()


# ── Rpc URL normalization ──────────────────────────────────────────────
def test_rpc_url_normalization():
    rpc = Rpc("wss://mainnet.helius-rpc.com/?api-key=test")
    assert rpc.url == "https://mainnet.helius-rpc.com/?api-key=test"
    rpc.close()
    rpc2 = Rpc("https://api.mainnet-beta.solana.com")
    assert rpc2.url == "https://api.mainnet-beta.solana.com"
    rpc2.close()


# ── Buy instruction ────────────────────────────────────────────────────
def test_build_buy_ix_data_and_accounts():
    _CREATOR_CACHE[TEST_MINT] = TEST_CREATOR
    ix = build_buy_ix(TEST_USER, TEST_MINT, 1_000_000, 1_000_000_000, TEST_FEE_RECIPIENT)
    # Data: disc(8) + amount(8) + max_sol_cost(8) + track_vol(1) + partial_fill(1) = 26
    assert len(ix.data) == 26
    assert ix.data[:8] == BUY_DISC
    assert struct.unpack("<Q", ix.data[8:16])[0] == 1_000_000
    assert struct.unpack("<Q", ix.data[16:24])[0] == 1_000_000_000
    assert ix.data[24] == 0  # track_volume = None → 0x00
    assert ix.data[25] == 0  # partial_fill = None → 0x00
    # 16 accounts
    assert len(ix.accounts) == 16
    # Verify key accounts
    assert str(ix.accounts[0].pubkey) == GLOBAL_PDA_ADDR       # global
    assert str(ix.accounts[6].pubkey) == TEST_USER              # user (signer)
    assert ix.accounts[6].is_signer and ix.accounts[6].is_writable
    assert str(ix.accounts[7].pubkey) == SYSTEM_PROGRAM          # system_program
    assert str(ix.accounts[8].pubkey) == TOKEN_PROGRAM           # token_program
    assert str(ix.accounts[15].pubkey) == "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"  # fee_program


def test_build_sell_ix_data_and_accounts():
    ix = build_sell_ix(TEST_USER, TEST_MINT, 5_000_000, 500_000_000,
                        TEST_FEE_RECIPIENT, TEST_CREATOR)
    # Data: disc(8) + amount(8) + min_sol_output(8) = 24
    assert len(ix.data) == 24
    assert ix.data[:8] == SELL_DISC
    assert struct.unpack("<Q", ix.data[8:16])[0] == 5_000_000
    assert struct.unpack("<Q", ix.data[16:24])[0] == 500_000_000
    assert len(ix.accounts) == 14
    assert str(ix.accounts[6].pubkey) == TEST_USER               # user (signer)
    assert ix.accounts[6].is_signer and ix.accounts[6].is_writable
    assert str(ix.accounts[7].pubkey) == SYSTEM_PROGRAM          # system_program
    # event_authority = PDA [b"__event_authority"]
    ea, _ = _find_pda([b"__event_authority"], PUMP_PROGRAM_ID)
    assert str(ix.accounts[10].pubkey) == str(ea)                # event_authority
    # fee_config = PDA [b"fee_config", FEE_CONFIG_CONST] in pump-fees program
    fc, _ = _find_pda([b"fee_config", FEE_CONFIG_CONST],
                      "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ")
    assert str(ix.accounts[12].pubkey) == str(fc)               # fee_config
    assert str(ix.accounts[13].pubkey) == "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"  # fee_program


# ── Mock RPC + LiveTrader tests ─────────────────────────────────────────
class MockRpc:
    """Deterministic RPC for testing LiveTrader without hitting the chain."""
    def __init__(self, balance_sol=1.0, fee_recipient=TEST_FEE_RECIPIENT,
                 confirmed_sigs=None):
        self.balance_sol = balance_sol
        self.fee_recipient = fee_recipient
        self._confirmed_sigs = set(confirmed_sigs or [])
        self.sent_count = 0

    def get_balance_sol(self, pubkey):
        return self.balance_sol

    def get_fee_recipient(self):
        return self.fee_recipient

    def fresh_blockhash(self):
        return bytes(32)

    def send_raw(self, raw_tx):
        sig = f"sig_{self.sent_count:08d}"
        self.sent_count += 1
        return sig

    def get_signature_statuses(self, sigs):
        return [({"err": None, "slot": 100} if s in self._confirmed_sigs else None)
                for s in sigs]

    def close(self):
        pass


def _make_trader(balance_sol=5.0, confirmed_sigs=None):
    from solders.keypair import Keypair
    _CREATOR_CACHE.clear()
    _CREATOR_CACHE[TEST_MINT] = TEST_CREATOR
    kp = Keypair()
    wallet = Wallet(kp, "test")
    trader = LiveTrader.__new__(LiveTrader)
    trader.rules = R
    trader.wallet = wallet
    trader.rpc = MockRpc(balance_sol=balance_sol, confirmed_sigs=confirmed_sigs)
    trader.max_sol_spend = 0.5
    trader.token_program = TOKEN_PROGRAM
    trader.data_dir = Path(tempfile.mkdtemp())
    trader.positions = {}
    trader.pending = {}
    trader.closed = []
    trader.traded = set()
    trader.events = []
    trader.cash = balance_sol
    trader._deployed = 0.0
    trader._fee_recipient = TEST_FEE_RECIPIENT
    trader._token_programs = {}
    trader._last_bal = balance_sol
    trader._last_bal_t = 0.0
    trader.started = 0.0
    trader._tokens = {}
    return trader


def _tok(mint=TEST_MINT):
    t = Token(mint, "TST", "Test", TEST_CREATOR, 0, 0.0)
    t.vsol, t.vtok, t.fee_bps = 40 * 10**9, 800 * 10**12, 125
    t.last_trade = 0.0
    return t


def test_request_entry_sends_buy_and_returns_sig():
    trader = _make_trader(balance_sol=5.0, confirmed_sigs={"sig_00000000"})
    t = _tok()
    assert trader.request_entry(t, now=100.0, equity=5.0, info={})
    assert TEST_MINT in trader.pending
    assert trader.pending[TEST_MINT].side == "buy"
    assert trader.pending[TEST_MINT].tx_sig == "sig_00000000"
    assert trader.traded == {TEST_MINT}


def test_request_entry_rejects_when_no_cash():
    trader = _make_trader(balance_sol=0.001)
    t = _tok()
    assert not trader.request_entry(t, now=100.0, equity=0.001, info={})


def test_request_entry_no_double_entry():
    trader = _make_trader(balance_sol=5.0)
    t = _tok()
    assert trader.request_entry(t, now=100.0, equity=5.0, info={})
    assert not trader.request_entry(t, now=101.0, equity=5.0, info={})


def test_buy_fill_confirms_and_creates_position():
    trader = _make_trader(balance_sol=5.0, confirmed_sigs={"sig_00000000"})
    t = _tok()
    trader.request_entry(t, now=100.0, equity=5.0, info={})
    trader.tick({t.mint: t}, now=100.5)
    assert TEST_MINT in trader.positions
    pos = trader.positions[TEST_MINT]
    assert pos.tokens > 0
    assert pos.cost_sol > 0
    assert TEST_MINT not in trader.pending


def test_stop_loss_triggers_sell():
    trader = _make_trader(balance_sol=5.0, confirmed_sigs={"sig_00000000"})
    t = _tok()
    trader.request_entry(t, now=100.0, equity=5.0, info={})
    trader.tick({t.mint: t}, now=100.5)  # buy confirmed → position
    # Drop price below stop_loss (35% drop → mult < 0.65)
    k = t.vsol * t.vtok
    t.vsol = int(math.sqrt(k * (t.vsol / t.vtok * 0.4)))
    t.vtok = int(k / t.vsol)
    t.last_trade = 101.0
    # Next tick → STOP sell sent
    trader.tick({t.mint: t}, now=102.0)
    assert TEST_MINT in trader.pending
    assert trader.pending[TEST_MINT].side == "sell"
    assert trader.pending[TEST_MINT].reason == "STOP"
    assert trader.pending[TEST_MINT].tx_sig == "sig_00000001"


def test_sell_confirmed_closes_position():
    trader = _make_trader(balance_sol=5.0,
                           confirmed_sigs={"sig_00000000", "sig_00000001"})
    t = _tok()
    trader.request_entry(t, now=100.0, equity=5.0, info={})
    trader.tick({t.mint: t}, now=100.5)  # buy confirmed → position
    # Price drops below stop-loss
    k = t.vsol * t.vtok
    t.vsol = int(math.sqrt(k * (t.vsol / t.vtok * 0.4)))
    t.vtok = int(k / t.vsol)
    t.last_trade = 101.0
    trader.tick({t.mint: t}, now=102.0)  # STOP → sell tx sent, pending
    assert TEST_MINT in trader.pending
    assert trader.pending[TEST_MINT].side == "sell"
    # Second tick → sell sig confirmed → position closed
    trader.tick({t.mint: t}, now=102.5)
    assert TEST_MINT not in trader.positions
    assert TEST_MINT not in trader.pending
    assert len(trader.closed) == 1
    assert trader.closed[0]["exit_reason"] == "STOP"


def test_manual_exit_sends_sell():
    trader = _make_trader(balance_sol=5.0, confirmed_sigs={"sig_00000000"})
    t = _tok()
    trader.request_entry(t, now=100.0, equity=5.0, info={})
    trader.tick({t.mint: t}, now=100.5)
    trader.set_tokens({t.mint: t})
    assert trader.request_exit(t.mint, now=200.0, reason="MANUAL")
    assert TEST_MINT in trader.pending
    assert trader.pending[TEST_MINT].side == "sell"
    assert trader.pending[TEST_MINT].reason == "MANUAL"


def test_partial_sell_for_take_profit():
    trader = _make_trader(balance_sol=5.0, confirmed_sigs={"sig_00000000"})
    t = _tok()
    trader.request_entry(t, now=100.0, equity=5.0, info={})
    trader.tick({t.mint: t}, now=100.5)
    # Move price up to trigger TP (50% sell at 2x)
    k = t.vsol * t.vtok
    t.vsol = int(math.sqrt(k * (t.vsol / t.vtok * 2.5)))
    t.vtok = int(k / t.vsol)
    t.last_trade = 101.0
    trader.tick({t.mint: t}, now=102.0)
    assert TEST_MINT in trader.pending
    assert trader.pending[TEST_MINT].side == "sell"
    assert trader.pending[TEST_MINT].reason == "TP"
    assert trader.pending[TEST_MINT].fraction == pytest.approx(0.5)


def test_equity_includes_cash_and_positions():
    trader = _make_trader(balance_sol=4.0)
    t = _tok()
    eq = trader.equity({t.mint: t})
    assert eq == pytest.approx(4.0)
    # ~100B raw tokens is roughly what a 0.1 SOL buy gets at this curve
    trader.positions[t.mint] = Position(t.mint, "TST", 100_000_000_000, 0.1, 0.1, 100.0,
                                        info={"tokens": 100_000_000_000})
    eq = trader.equity({t.mint: t})
    assert 4.0 < eq < 4.1  # position value is less than cost due to fees/slippage


def test_to_state_roundtrip():
    trader = _make_trader(balance_sol=5.0)
    t = _tok()
    trader.positions[t.mint] = Position(t.mint, "TST", 1_000_000, 0.1, 0.1, 100.0,
                                        info={"tokens": 1_000_000})
    trader.pending["OTHER"] = Order("OTHER", "buy", 200.0, budget=0.1, tx_sig="sig_002")
    trader._deployed = 0.1
    st = trader.to_state()
    assert len(st["positions"]) == 1
    assert st["positions"][0]["mint"] == t.mint
    assert len(st["pending"]) == 1
    assert st["pending"][0]["tx_sig"] == "sig_002"


# ── Engine integration ──────────────────────────────────────────────────
def test_engine_with_live_config():
    from solders.keypair import Keypair
    from pumpdesk.engine import Engine
    kp = Keypair()
    wallet = Wallet(kp, "test")
    lc = LiveConfig(wallet=wallet, http_rpc="https://api.mainnet-beta.solana.com")
    with tempfile.TemporaryDirectory() as d:
        eng = Engine(R, data_dir=Path(d), live_config=lc)
        assert isinstance(eng.paper, LiveTrader)
        snap = eng.snapshot(time.time())  # noqa: F821
        assert "account" in snap
        eng.paper.rpc.close()


def test_engine_paper_mode_by_default():
    from pumpdesk.engine import Engine
    with tempfile.TemporaryDirectory() as d:
        eng = Engine(R, data_dir=Path(d))
        from pumpdesk.paper import Paper
        assert isinstance(eng.paper, Paper)


def test_engine_reset_account_uses_live_trader():
    from solders.keypair import Keypair
    from pumpdesk.engine import Engine
    kp = Keypair()
    wallet = Wallet(kp, "test")
    lc = LiveConfig(wallet=wallet, http_rpc="https://api.mainnet-beta.solana.com")
    with tempfile.TemporaryDirectory() as d:
        eng = Engine(R, data_dir=Path(d), live_config=lc)
        eng.paper._deployed = 0.3
        eng.reset_account()
        assert isinstance(eng.paper, LiveTrader)
        assert eng.paper._deployed == 0.0
        eng.paper.rpc.close()
