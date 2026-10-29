"""pump.fun bonding-curve math (constant product on virtual reserves).

All amounts are raw integers: lamports for SOL, 1e-6 units for tokens.
"""
from __future__ import annotations

INITIAL_REAL_TOKENS = 793_100_000_000_000  # tokens sellable before graduation


def progress_pct(real_token_reserves: int) -> float:
    left = max(0, min(real_token_reserves, INITIAL_REAL_TOKENS))
    return (1 - left / INITIAL_REAL_TOKENS) * 100


def price_sol(vsol: int, vtok: int) -> float:
    """SOL per whole token."""
    return (vsol / 1e9) / (vtok / 1e6)


def tokens_out(vsol: int, vtok: int, sol_in_net: int) -> int:
    """Tokens received for `sol_in_net` lamports reaching the curve (fees excluded)."""
    return vtok - (vsol * vtok) // (vsol + sol_in_net) - 1 if sol_in_net > 0 else 0


def sol_out(vsol: int, vtok: int, tokens_in: int) -> int:
    """Gross lamports out of the curve for selling `tokens_in` (before fees)."""
    return vsol - (vsol * vtok) // (vtok + tokens_in) - 1 if tokens_in > 0 else 0


def net_of_buy_fee(budget: int, fee_bps: int) -> int:
    """Lamports that reach the curve when `budget` must also cover the fee."""
    return int(budget * 10_000 / (10_000 + fee_bps))


def buy_with_budget(vsol: int, vtok: int, budget: int, fee_bps: int) -> int:
    return tokens_out(vsol, vtok, net_of_buy_fee(budget, fee_bps))


def sell_net(vsol: int, vtok: int, tokens_in: int, fee_bps: int) -> int:
    gross = sol_out(vsol, vtok, tokens_in)
    return gross - gross * fee_bps // 10_000
