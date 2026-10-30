"""Live trading on pump.fun: sends real Solana transactions.

Mirrors the ``Paper`` interface (``request_entry``, ``request_exit``,
``on_update``, ``tick``, ``equity``, ``mark_value``, ``mark_mult``) but
actually builds and sends Solana transactions to the pump.fun program.

Confirmation is driven by polling ``getSignatureStatuses``: when our
buy/sell transaction lands, ``tick`` detects it and fills the order.

Safety:
- Paper mode is the default.  Live trading requires ``WALLET_FILE`` to be
  set in ``.env`` AND ``LIVE=true``.
- ``max_sol_spend`` caps total live deployment.
- ``max_sol_cost`` on each buy and ``min_sol_output`` on each sell provide
  slippage protection.
"""
from __future__ import annotations

import base64
import json
import logging
import struct
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from .curve import buy_with_budget, sell_net
from .decoder import PUMP_PROGRAM, LAMPORTS, TOTAL_SUPPLY_RAW
from .paper import Position, Order
from .rules import Rules
from .tracker import Token

log = logging.getLogger("pumpdesk.live")

# ── Program IDs ──────────────────────────────────────────────────────────
PUMP_PROGRAM_ID = PUMP_PROGRAM  # 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P
PUMP_FEES_PROGRAM = "pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJybLUhqVgwRMpWhqfHh8B7dNXs"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
ASSOCIATED_TOKEN_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
RENT_SYSVAR = "SysvarRent11111111111111111111111111111112"
WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"

# ── Instruction discriminators (sha256("global:b" + name)[:8]) ───────────
BUY_DISC = bytes([102, 6, 61, 18, 1, 218, 235, 234])  # buy
SELL_DISC = bytes([51, 230, 133, 164, 1, 127, 131, 173])  # sell

# ── PDA seeds ────────────────────────────────────────────────────────────
GLOBAL_SEED = b"global"
BONDING_CURVE_SEED = b"bonding-curve"
CREATOR_VAULT_SEED = b"creator-vault"
EVENT_AUTHORITY_SEED = b"__event_authority"
FEE_CONFIG_SEED = b"fee_config"
FEE_CONFIG_CONST = bytes.fromhex(
    "0156e0f693665acf44db1568bf175baa5189cb97f5d2ff3b655d2bb6fd6d18b0"
)
USER_VOLUME_ACC_SEED = b"user_volume_accumulator"
GLOBAL_VOLUME_ACC_SEED = b"global_volume_accumulator"

# ── Known PDAs ──────────────────────────────────────────────────────────
GLOBAL_PDA_ADDR = "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf"
FEE_RECIPIENT_OFFSET = 41  # byte offset inside Global account data


# ── Pubkey helpers ──────────────────────────────────────────────────────
def _b58(p: str) -> bytes:
    """Encode a base58 string into raw 32 bytes."""
    from solders.pubkey import Pubkey
    return bytes(Pubkey.from_string(p))


def _find_pda(seeds: list[bytes], program: str) -> tuple:
    """Derive a PDA. Returns (Pubkey, bump)."""
    from solders.pubkey import Pubkey
    pk_seeds = [s if isinstance(s, bytes) else bytes(s) for s in seeds]
    return Pubkey.find_program_address(pk_seeds, Pubkey.from_string(program))


def _ata(wallet: str, mint: str, token_program: str = TOKEN_PROGRAM) -> str:
    """Associated Token Account address for *wallet* holding *mint*."""
    from solders.pubkey import Pubkey
    w, t, m = (Pubkey.from_string(x) for x in (wallet, token_program, mint))
    pub, _ = Pubkey.find_program_address(
        [bytes(w), bytes(t), bytes(m)],
        Pubkey.from_string(ASSOCIATED_TOKEN_PROGRAM),
    )
    return str(pub)


def _option_bool(val):
    """Encode an Anchor OptionBool: a single byte.

    Per the pump.fun IDL, OptionBool is a newtype ``struct OptionBool(bool)``
    encoded as one byte.  The public docs confirm: ``{0}`` = false/None,
    ``{1}`` = true.
    """
    if val is None:
        return b"\x00"
    return b"\x01" if val else b"\x00"


# ── Wallet ───────────────────────────────────────────────────────────────
@dataclass
class Wallet:
    """Ed25519 keypair loaded from a Solana CLI-format JSON file."""
    keypair: object   # solders Keypair
    path: str

    @property
    def pubkey(self) -> str:
        return str(self.keypair.pubkey())

    @classmethod
    def from_file(cls, path: str | Path) -> "Wallet":
        text = Path(path).read_text(encoding="utf-8")
        secret = json.loads(text)
        if not isinstance(secret, list) or len(secret) != 64:
            raise ValueError(
                f"Wallet file must contain a 64-byte Solana secret key "
                f"(CLI format array of 64 ints). Got {type(secret).__name__} "
                f"len={len(secret) if isinstance(secret, list) else 'n/a'}."
            )
        from solders.keypair import Keypair
        kp = Keypair.from_bytes(bytes(secret))
        return cls(kp, str(path))


# ── RPC ──────────────────────────────────────────────────────────────────
class Rpc:
    """Synchronous JSON-RPC client (httpx).  Fast enough for trade-sized ops."""
    def __init__(self, http_url: str):
        if http_url.startswith("wss://"):
            http_url = "https://" + http_url[len("wss://"):]
        elif http_url.startswith("ws://"):
            http_url = "http://" + http_url[len("ws://"):]
        self.url = http_url
        self._client = httpx.Client(timeout=30)
        self._blockhash: bytes | None = None
        self._bh_time = 0.0

    def close(self):
        self._client.close()

    # ── low level ──
    def _post(self, method: str, params: list):
        resp = self._client.post(self.url, json={
            "jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        r = resp.json()
        if r.get("error"):
            raise RuntimeError(f"RPC {method}: {r['error']}")
        return r["result"]

    # ── high level ──
    def get_balance_sol(self, pubkey: str) -> float:
        r = self._post("getBalance", [pubkey, {"commitment": "confirmed"}])
        return int(r["value"]) / LAMPORTS

    def get_fee_recipient(self) -> str:
        r = self._post("getAccountInfo", [GLOBAL_PDA_ADDR,
                          {"encoding": "base64", "commitment": "confirmed"}])
        val = r.get("value")
        if not val:
            return ""
        raw = base64.b64decode(val["data"][0])
        from solders.pubkey import Pubkey
        return str(Pubkey.from_bytes(raw[FEE_RECIPIENT_OFFSET:FEE_RECIPIENT_OFFSET + 32]))

    def get_bonding_curve(self, mint: str) -> dict | None:
        """Fetch the BondingCurve account to get creator + current reserves."""
        bc, _ = _find_pda([BONDING_CURVE_SEED, _b58(mint)], PUMP_PROGRAM_ID)
        r = self._post("getAccountInfo", [str(bc),
                          {"encoding": "base64", "commitment": "confirmed"}])
        val = r.get("value")
        if not val:
            return None
        raw = base64.b64decode(val["data"][0])
        if len(raw) < 64:
            return None
        off = 8  # Anchor discriminator
        virtual_token = int.from_bytes(raw[off:off+8], "little"); off += 8
        virtual_sol = int.from_bytes(raw[off:off+8], "little"); off += 8
        real_token = int.from_bytes(raw[off:off+8], "little"); off += 8
        real_sol = int.from_bytes(raw[off:off+8], "little"); off += 8
        token_total = int.from_bytes(raw[off:off+8], "little"); off += 8
        complete = raw[off] != 0; off += 1
        creator = None
        if len(raw) > off + 32:
            from solders.pubkey import Pubkey
            creator = str(Pubkey.from_bytes(raw[off:off+32]))
        return {
            "vsol": virtual_sol, "vtok": virtual_token,
            "rsol": real_sol, "rtok": real_token,
            "total_supply": token_total, "complete": complete,
            "creator": creator,
        }

    def get_mint_program(self, mint: str) -> str:
        """Detect whether *mint* is owned by legacy Token or Token-2022 program."""
        r = self._post("getAccountInfo", [mint, {"encoding": "jsonParsed", "commitment": "confirmed"}])
        val = r.get("value")
        if not val:
            return TOKEN_PROGRAM  # fallback
        return val.get("owner", TOKEN_PROGRAM)


    def fresh_blockhash(self) -> bytes:
        now = time.time()
        if self._blockhash and now - self._bh_time < 30:
            return self._blockhash
        r = self._post("getLatestBlockhash", [{"commitment": "confirmed"}])
        self._blockhash = bytes.fromhex(r["value"]["blockhash"])
        self._bh_time = now
        return self._blockhash

    def send_raw(self, raw_tx: bytes) -> str:
        r = self._post("sendRawTransaction", [
            base64.b64encode(raw_tx).decode(), {"skipPreflight": False, "preflightCommitment": "confirmed"}
        ])
        return r  # signature

    def get_signature_statuses(self, sigs: list[str]) -> list:
        if not sigs:
            return []
        r = self._post("getSignatureStatuses", [sigs, {"searchTransactionHistory": False}])
        return r["value"]


# ── Instruction builders ──────────────────────────────────────────────────
def _derive_buy_accounts(wallet_pub: str, mint: str, fee_recipient: str,
                         token_program: str) -> list:
    """Return the 16 AccountMeta for the ``buy`` instruction."""
    from solders.pubkey import Pubkey
    from solders.instruction import AccountMeta

    KP = lambda s: Pubkey.from_string(s)  # noqa: E731
    bc, _ = _find_pda([BONDING_CURVE_SEED, _b58(mint)], PUMP_PROGRAM_ID)
    abc = _ata(str(bc), mint, token_program)
    auser = _ata(wallet_pub, mint, token_program)
    cv, _ = _find_pda([CREATOR_VAULT_SEED, _b58(_CREATOR_CACHE[mint])], PUMP_PROGRAM_ID)
    gva, _ = _find_pda([GLOBAL_VOLUME_ACC_SEED], PUMP_PROGRAM_ID)
    uva, _ = _find_pda([USER_VOLUME_ACC_SEED, _b58(wallet_pub)], PUMP_PROGRAM_ID)
    fc, _ = _find_pda([FEE_CONFIG_SEED, FEE_CONFIG_CONST], PUMP_FEES_PROGRAM)
    ea, _ = _find_pda([EVENT_AUTHORITY_SEED], PUMP_PROGRAM_ID)
    g, _ = _find_pda([GLOBAL_SEED], PUMP_PROGRAM_ID)

    return [
        AccountMeta(KP(str(g)),      False, False),  # global
        AccountMeta(KP(fee_recipient), False, True),   # fee_recipient
        AccountMeta(KP(mint),        False, False),   # mint
        AccountMeta(KP(str(bc)),     False, True),   # bonding_curve
        AccountMeta(KP(abc),         False, True),   # associated_bonding_curve
        AccountMeta(KP(auser),       False, True),   # associated_user
        AccountMeta(KP(wallet_pub),  True,  True),   # user
        AccountMeta(KP(SYSTEM_PROGRAM), False, False),  # system_program
        AccountMeta(KP(token_program),  False, False),   # token_program
        AccountMeta(KP(str(cv)),     False, True),   # creator_vault
        AccountMeta(KP(str(ea)),     False, False),  # event_authority
        AccountMeta(KP(PUMP_PROGRAM_ID), False, False),  # program
        AccountMeta(KP(str(gva)),    False, False),   # global_volume_accumulator
        AccountMeta(KP(str(uva)),    False, True),   # user_volume_accumulator
        AccountMeta(KP(str(fc)),     False, False),   # fee_config
        AccountMeta(KP(PUMP_FEES_PROGRAM), False, False),  # fee_program
    ]


_CREATOR_CACHE: dict[str, str] = {}


def _derive_sell_accounts(wallet_pub: str, mint: str, fee_recipient: str,
                          creator: str, token_program: str) -> list:
    """Return the 14 AccountMeta for the ``sell`` instruction."""
    from solders.pubkey import Pubkey
    from solders.instruction import AccountMeta

    KP = lambda s: Pubkey.from_string(s)  # noqa: E731
    bc, _ = _find_pda([BONDING_CURVE_SEED, _b58(mint)], PUMP_PROGRAM_ID)
    abc = _ata(str(bc), mint, token_program)
    auser = _ata(wallet_pub, mint, token_program)
    cv, _ = _find_pda([CREATOR_VAULT_SEED, _b58(creator)], PUMP_PROGRAM_ID)
    fc, _ = _find_pda([FEE_CONFIG_SEED, FEE_CONFIG_CONST], PUMP_FEES_PROGRAM)
    ea, _ = _find_pda([EVENT_AUTHORITY_SEED], PUMP_PROGRAM_ID)

    return [
        AccountMeta(KP(GLOBAL_PDA_ADDR),        False, False),  # global
        AccountMeta(KP(fee_recipient),          False, True),   # fee_recipient
        AccountMeta(KP(mint),                  False, False),   # mint
        AccountMeta(KP(str(bc)),               False, True),   # bonding_curve
        AccountMeta(KP(abc),                   False, True),   # associated_bonding_curve
        AccountMeta(KP(auser),                 False, True),   # associated_user
        AccountMeta(KP(wallet_pub),            True,  True),   # user
        AccountMeta(KP(SYSTEM_PROGRAM),        False, False),   # system_program
        AccountMeta(KP(str(cv)),               False, True),   # creator_vault
        AccountMeta(KP(token_program),         False, False),   # token_program
        AccountMeta(KP(str(ea)),               False, False),   # event_authority
        AccountMeta(KP(PUMP_PROGRAM_ID),       False, False),  # program
        AccountMeta(KP(str(fc)),               False, False),   # fee_config
        AccountMeta(KP(PUMP_FEES_PROGRAM),     False, False),  # fee_program
    ]


def build_buy_ix(wallet_pub: str, mint: str, amount: int, max_sol_cost: int,
                 fee_recipient: str, token_program: str = TOKEN_PROGRAM):
    """Build a pump.fun ``buy`` instruction (legacy, 16 accounts)."""
    from solders.pubkey import Pubkey
    from solders.instruction import Instruction
    accounts = _derive_buy_accounts(wallet_pub, mint, fee_recipient, token_program)
    data = (BUY_DISC
            + struct.pack("<Q", amount)
            + struct.pack("<Q", max_sol_cost)
            + _option_bool(None)   # track_volume = None
            + _option_bool(None))  # partial_fill = None
    return Instruction(program_id=Pubkey.from_string(PUMP_PROGRAM_ID), data=data, accounts=accounts)


def build_sell_ix(wallet_pub: str, mint: str, amount: int, min_sol_output: int,
                  fee_recipient: str, creator: str,
                  token_program: str = TOKEN_PROGRAM):
    """Build a pump.fun ``sell`` instruction (legacy, 14 accounts)."""
    from solders.pubkey import Pubkey
    from solders.instruction import Instruction
    accounts = _derive_sell_accounts(wallet_pub, mint, fee_recipient, creator, token_program)
    data = SELL_DISC + struct.pack("<Q", amount) + struct.pack("<Q", min_sol_output)
    return Instruction(program_id=Pubkey.from_string(PUMP_PROGRAM_ID), data=data, accounts=accounts)


# ── Config ──────────────────────────────────────────────────────────────
@dataclass
class LiveConfig:
    wallet: Wallet
    http_rpc: str
    max_sol_spend: float = 0.5          # absolute cap on total live deployment
    token_program: str = TOKEN_PROGRAM  # TOKEN_PROGRAM or TOKEN_2022_PROGRAM
    commitment: str = "confirmed"


# ── Live trader ──────────────────────────────────────────────────────────
class LiveTrader:
    """Drop-in replacement for ``Paper`` that sends real transactions.

    Mirrors the same public attributes (``positions``, ``pending``,
    ``closed``, ``events``, ``traded``, ``cash``) and methods so the
    existing ``Engine`` needs no changes beyond construction.
    """

    def __init__(self, rules: Rules, config: LiveConfig, data_dir: Path):
        self.rules = rules
        self.wallet = config.wallet
        self.rpc = Rpc(config.http_rpc)
        self.max_sol_spend = config.max_sol_spend
        self.token_program = config.token_program
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Same interface as Paper ─────────────────────────────────
        self.positions: dict[str, Position] = {}
        self.pending: dict[str, Order] = {}
        self.closed: list[dict] = []
        self.traded: set[str] = set()
        self.events: list[dict] = []
        self.cash: float = 0.0

        self._deployed: float = 0.0
        self._fee_recipient: str = ""
        self._token_programs: dict[str, str] = {}  # mint → "Token..."|Token2022"
        self._last_bal = 0.0
        self._last_bal_t = 0.0
        self.started = time.time()

    # ── helpers (shared with Paper) ──────────────────────────────────
    def _slip(self) -> float:
        return 1 - self.rules.sim.extra_slippage_bps / 10_000

    def _proceeds(self, t: Token, tokens: int) -> float:
        gross = sell_net(t.vsol, t.vtok, tokens, t.fee_bps) / LAMPORTS * self._slip()
        return gross - self.rules.sim.tx_cost_sol

    def mark_value(self, pos: Position, t: Token) -> float:
        return max(0.0, self._proceeds(t, pos.tokens)) if pos.tokens > 0 else 0.0

    def mark_mult(self, pos: Position, t: Token) -> float:
        return self.mark_value(pos, t) / pos.cost_sol if pos.cost_sol > 0 else 0.0

    def equity(self, tokens: dict) -> float:
        eq = self.cash + sum(o.budget for o in self.pending.values() if o.side == "buy")
        for m, pos in self.positions.items():
            t = tokens.get(m)
            eq += self.mark_value(pos, t) if t else pos.cost_sol
        return eq

    # ── balance ──────────────────────────────────────────────────────
    def _refresh_balance(self):
        """Update ``self.cash`` from the chain (cached for 4s)."""
        now = time.time()
        if now - self._last_bal_t < 4:
            return
        try:
            self.cash = self.rpc.get_balance_sol(self.wallet.pubkey)
            self._last_bal_t = now
            self._last_bal = self.cash
        except Exception as e:
            log.warning("balance refresh failed: %s", e)

    def _check_spend_cap(self) -> bool:
        return self._deployed <= self.max_sol_spend

    def _ensure_fee_recipient(self) -> str:
        if not self._fee_recipient:
            try:
                self._fee_recipient = self.rpc.get_fee_recipient()
            except Exception as e:
                log.error("fetch fee_recipient: %s", e)
        return self._fee_recipient

    def _detect_token_program(self, mint: str) -> str:
        """Determine Token vs Token-2022 program for *mint* (cached)."""
        if mint not in self._token_programs:
            try:
                self._token_programs[mint] = self.rpc.get_mint_program(mint)
            except Exception as e:
                log.warning("token program detection failed for %s: %s; using default", mint, e)
                self._token_programs[mint] = self.token_program
        return self._token_programs[mint]

    # ── entry ────────────────────────────────────────────────────────
    def request_entry(self, t: Token, now: float, equity: float, info: dict) -> bool:
        a = self.rules.account
        if t.mint in self.traded or t.mint in self.pending:
            return False
        if len(self.positions) + sum(o.side == "buy" for o in self.pending.values()) >= a.max_open_positions:
            return False
        if not self._check_spend_cap():
            log.warning("max_sol_spend (%.2f SOL) reached; skipping entry", self.max_sol_spend)
            return False

        self._refresh_balance()
        budget_sol = min(equity * a.risk_per_trade_pct / 100, self.cash)
        if budget_sol <= self.rules.sim.tx_cost_sol * 4:
            return False

        budget_lamports = int(budget_sol * LAMPORTS)
        # Reduce by extra slippage + tx cost to get what reaches the curve
        fee_budget = int(budget_lamports * (1 - self.rules.sim.extra_slippage_bps / 10_000)
                         - self.rules.sim.tx_cost_sol * LAMPORTS)

        token_amount = buy_with_budget(t.vsol, t.vtok, fee_budget, t.fee_bps)
        if token_amount <= 0:
            return False

        fee_recipient = self._ensure_fee_recipient()
        if not fee_recipient:
            log.error("Could not determine fee_recipient; skipping entry")
            return False

        tok_prog = self._detect_token_program(t.mint)
        _CREATOR_CACHE.setdefault(t.mint, t.creator)

        try:
            ix = build_buy_ix(
                self.wallet.pubkey, str(t.mint), token_amount, budget_lamports,
                fee_recipient, tok_prog,
            )
            sig = self._send_and_record(ix)
        except Exception as e:
            log.exception("buy tx failed: %s", e)
            return False

        self.traded.add(t.mint)
        entry_info = dict(info)
        entry_info["tokens"] = token_amount
        self.pending[t.mint] = Order(
            t.mint, "buy", now + self.rules.sim.latency_ms / 1000,
            budget=budget_sol, info=entry_info, symbol=t.symbol, tx_sig=sig,
        )
        self._deployed += budget_sol
        log.info("sent BUY %s %d tokens for %.4f SOL (sig %s)", t.symbol, token_amount, budget_sol, sig[:12])
        return True

    # ── exit ──────────────────────────────────────────────────────────
    def request_exit(self, mint: str, now: float, reason: str = "MANUAL", fraction: float = 1.0) -> bool:
        if mint not in self.positions:
            return False
        pos = self.positions[mint]
        t = getattr(self, "_tokens", {}).get(mint)
        if t is None:
            return False

        sell_tokens = pos.tokens if fraction >= 1.0 else int(pos.tokens * fraction)
        gross_lamports = sell_net(t.vsol, t.vtok, sell_tokens, t.fee_bps)
        min_out = int(gross_lamports * self._slip()
                      - self.rules.sim.tx_cost_sol * LAMPORTS * 0.5)
        creator = _CREATOR_CACHE.get(mint, "")
        if not creator:
            _CREATOR_CACHE.setdefault(mint, "")
            return False
        fee_recipient = self._ensure_fee_recipient()
        if not fee_recipient:
            return False

        tok_prog = self._detect_token_program(mint)

        try:
            ix = build_sell_ix(
                self.wallet.pubkey, str(mint), sell_tokens, min_out,
                fee_recipient, creator, tok_prog,
            )
            sig = self._send_and_record(ix)
        except Exception as e:
            log.exception("sell tx failed: %s", e)
            return False

        self.pending[mint] = Order(
            mint, "sell", now + self.rules.sim.latency_ms / 1000,
            fraction=fraction, reason=reason, tx_sig=sig,
        )
        log.info("sent SELL %s %d/%d tokens %s (sig %s)", pos.symbol, sell_tokens, pos.tokens, reason, sig[:12])
        return True

    # ── event / tick processing ────────────────────────────────────────
    def set_tokens(self, tokens: dict):
        """Called by Engine so we can read current curve data for mark/pricing."""
        self._tokens = tokens

    def on_update(self, t: Token, now: float):
        """Check pending tx confirmations for this token; then check exits."""
        o = self.pending.get(t.mint)
        if o and o.tx_sig and self._is_confirmed(o.tx_sig):
            self._fill_confirmed(o, t, now)
        pos = self.positions.get(t.mint)
        if pos and t.mint not in self.pending:
            self._check_exits(pos, t, now)

    def tick(self, tokens: dict, now: float):
        """Poll pending signatures, fill confirmed ones, check exits."""
        self.set_tokens(tokens)
        self._refresh_balance()

        sigs = [o.tx_sig for o in self.pending.values() if o.tx_sig]
        if sigs:
            statuses = self.rpc.get_signature_statuses(sigs)
            for o in list(self.pending.values()):
                if not o.tx_sig:
                    continue
                idx = sigs.index(o.tx_sig)
                if idx < len(statuses) and statuses[idx] is not None:
                    if statuses[idx].get("err") is None:
                        self._fill_confirmed(o, tokens.get(o.mint), now)

        # Check exits for all positions (same logic as Paper._check_exits)
        for m, pos in list(self.positions.items()):
            t = tokens.get(m)
            if t and m not in self.pending:
                self._check_exits(pos, t, now)

    # ── confirmation ──────────────────────────────────────────────────
    def _is_confirmed(self, sig: str) -> bool:
        if not sig:
            return True
        try:
            statuses = self.rpc.get_signature_statuses([sig])
            if statuses and statuses[0] is not None:
                return statuses[0].get("err") is None
        except Exception:
            pass
        return False

    def _fill_confirmed(self, o: Order, t: Token | None, now: float):
        """Fill a confirmed buy/sell transaction."""
        self.pending.pop(o.mint, None)

        if o.side == "buy":
            self.positions[o.mint] = Position(
                o.mint, o.symbol or (t.symbol if t else "???"),
                o.info.get("tokens", 0), o.budget, o.budget, now, info=o.info,
            )
            self.events.append({"type": "entry", "mint": o.mint,
                                "symbol": o.symbol or (t.symbol if t else ""),
                                "sol": o.budget, "price": t.price if t else 0,
                                "mcap_sol": t.mcap_sol if t else 0,
                                "curve_pct": round(t.curve_pct, 0) if t else 0, "at": now})
        else:
            pos = self.positions.get(o.mint)
            if pos and t:
                q = pos.tokens if o.fraction >= 1 else int(pos.tokens * o.fraction)
                proceeds = max(0.0, self._proceeds(t, q))
                self.cash += proceeds
                pos.proceeds += proceeds
                pos.cost_sol -= pos.cost_sol * q / pos.tokens
                pos.tokens -= q
                pos.exits.append(o.reason)
                if o.reason == "TP":
                    pos.tp_done = True
                fully_closed = pos.tokens <= 0 or o.fraction >= 1
                self.events.append({"type": "exit", "mint": o.mint, "symbol": pos.symbol,
                                    "reason": o.reason, "sol": proceeds,
                                    "partial": not fully_closed, "at": now})
                if fully_closed:
                    del self.positions[o.mint]
                    pnl = pos.proceeds - pos.total_cost
                    self.closed.append({
                        "mint": pos.mint, "symbol": pos.symbol,
                        "opened_at": pos.opened_at, "closed_at": now,
                        "hold_min": round((now - pos.opened_at) / 60, 2),
                        "cost_sol": round(pos.total_cost, 6),
                        "proceeds_sol": round(pos.proceeds, 6),
                        "pnl_sol": round(pnl, 6),
                        "pnl_pct": round(pnl / pos.total_cost * 100, 2) if pos.total_cost else 0,
                        "peak_mult": round(pos.peak_mult, 3),
                        "exit_reason": "+".join(pos.exits),
                        **{f"entry_{k}": v for k, v in pos.info.items()},
                    })

    # ── exit checks (same logic as paper.py) ──────────────────────────
    def _check_exits(self, pos: Position, t: Token, now: float):
        x = self.rules.exit
        if t.graduated and x.exit_on_graduation:
            self._send_exit(pos, t, now, "GRADUATED")
            return
        mult = self.mark_mult(pos, t)
        pos.last_mult = mult
        pos.peak_mult = max(pos.peak_mult, mult)
        if mult <= 1 - x.stop_loss_pct / 100:
            self._send_exit(pos, t, now, "STOP")
        elif pos.tp_done and x.breakeven_after_tp and mult <= 1.0:
            self._send_exit(pos, t, now, "BREAKEVEN")
        elif not pos.tp_done and mult >= x.take_profit_multiple:
            self._send_exit(pos, t, now, "TP", fraction=x.take_profit_sell_pct / 100)
        elif now - pos.opened_at >= x.max_hold_min * 60:
            self._send_exit(pos, t, now, "TIME")
        elif now - t.last_trade >= x.stale_exit_min * 60:
            self._send_exit(pos, t, now, "STALE")

    def _send_exit(self, pos: Position, t: Token, now: float, reason: str, fraction: float = 1.0):
        if pos.mint in self.pending:
            return
        self.request_exit(pos.mint, now, reason=reason, fraction=fraction)

    # ── tx sending ────────────────────────────────────────────────────
    def _send_and_record(self, ix) -> str:
        from solders.transaction import VersionedTransaction
        from solders.message import MessageV0
        from solders.hash import Hash
        from solders.pubkey import Pubkey
        bh = self.rpc.fresh_blockhash()
        msg = MessageV0.try_compile(
            payer=Pubkey.from_string(self.wallet.pubkey),
            instructions=[ix],
            address_lookup_table_accounts=[],
            recent_blockhash=Hash.from_bytes(bh),
        )
        tx = VersionedTransaction(msg, [self.wallet.keypair])
        raw = bytes(tx)
        return self.rpc.send_raw(raw)

    # ── persistence ───────────────────────────────────────────────────
    def to_state(self) -> dict:
        return {"positions": [asdict(p) for p in self.positions.values()],
                "pending": [asdict(o) for o in self.pending.values()],
                "deployed": self._deployed}

    def from_state(self, st: dict):
        for d in st.get("positions", []):
            self.positions[d["mint"]] = Position(**d)
        for d in st.get("pending", []):
            self.pending[d["mint"]] = Order(**d)
        self._deployed = st.get("deployed", 0.0)
        self.traded = set(st.get("traded", []))
        # Refresh cash from the chain — don't trust paper state for live balances
        self._refresh_balance()
