"""pump.fun bonding-curve math (constant product on virtual reserves).

All amounts are raw integers: lamports for SOL, 1e-6 units for tokens.
"""
from __future__ import annotations

INITIAL_REAL_TOKENS = 793_100_000_000_000  # tokens sellable before graduation

INITIAL_VSOL = 30 * 10**9
INITIAL_VTOK = 1_073_000_000 * 10**6
K_STANDARD = INITIAL_VSOL * INITIAL_VTOK
VTOK_OFFSET = INITIAL_VTOK - INITIAL_REAL_TOKENS


def is_standard(vsol: int, vtok: int, rtok: int) -> bool:
    """Validate curve invariants to reject non-standard or manipulated pools."""
    if vsol <= 0 or vtok <= 0:
        return False
    # Check invariant k within 1% tolerance
    k = vsol * vtok
    if abs(k / K_STANDARD - 1) >= 0.01:
        return False
    # Check virtual/real token alignment
    return abs((vtok - rtok) - VTOK_OFFSET) < 10**11


def progress_pct(real_token_reserves: int) -> float:
    """Returns curve graduation progress from 0.0% to 100.0%."""
    left = max(0, min(real_token_reserves, INITIAL_REAL_TOKENS))
    return (1.0 - (left / INITIAL_REAL_TOKENS)) * 100.0


def price_sol(vsol: int, vtok: int) -> float:
    """SOL price per whole token (1e6 base units)."""
    if vtok <= 0:
        return 0.0
    return (vsol / vtok) * 1e-3


def tokens_out(vsol: int, vtok: int, sol_in_net: int) -> int:
    """Tokens received for `sol_in_net` lamports reaching the curve (fees excluded)."""
    if sol_in_net <= 0 or vsol <= 0 or vtok <= 0:
        return 0
    # dy = vtok - (k / (vsol + dx))
    new_vtok = (vsol * vtok) // (vsol + sol_in_net)
    out = vtok - new_vtok
    return max(0, out)


def sol_out(vsol: int, vtok: int, tokens_in: int) -> int:
    """Gross lamports out of the curve for selling `tokens_in` (before fees)."""
    if tokens_in <= 0 or vsol <= 0 or vtok <= 0:
        return 0
    # dx = vsol - (k / (vtok + dy))
    new_vsol = (vsol * vtok) // (vtok + tokens_in)
    out = vsol - new_vsol
    return max(0, out)


def net_of_buy_fee(budget: int, fee_bps: int) -> int:
    """Lamports reaching the curve when `budget` must also cover the protocol fee."""
    if budget <= 0:
        return 0
    return (budget * 10_000) // (10_000 + fee_bps)


def buy_with_budget(vsol: int, vtok: int, budget: int, fee_bps: int) -> int:
    """Tokens acquired when spending total `budget` lamports (including fee)."""
    net_sol = net_of_buy_fee(budget, fee_bps)
    return tokens_out(vsol, vtok, net_sol)


def sell_net(vsol: int, vtok: int, tokens_in: int, fee_bps: int) -> int:
    """Net lamports returned to wallet after deducting protocol fee (ceiling division)."""
    gross = sol_out(vsol, vtok, tokens_in)
    if gross <= 0:
        return 0
    # Ceiling division on fee matches on-chain behavior
    fee = (gross * fee_bps + 9_999) // 10_000
    return max(0, gross - fee)
