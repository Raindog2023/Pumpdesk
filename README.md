# PumpDesk

A pump.fun scanner and **paper-trading** desk for Windows. It watches every new
pump.fun launch on-chain, scores each one against your rules, and "trades" the ones
that pass with fake SOL at realistic prices. The goal is to find out whether your
rules make money **before** you risk real SOL.

> **No wallet. No keys. No real trades.** PumpDesk only reads public blockchain data.
> When you trade for real, you still click the buy yourself in Axiom, Photon or GMGN.

## Install (Windows 11)

1. On GitHub, open the repo, click **Code → Download ZIP**, and unzip it somewhere
   permanent (for example `C:\PumpDesk`).
2. Double-click **`Install-PumpDesk.bat`**.
   - If Python is missing, it installs Python 3.12 via winget. Then run it again.
   - It creates a **PumpDesk** icon on your Desktop and in the Start Menu.
3. Notepad opens `.env`. Fill in:
   - `TG_TOKEN`: your Telegram bot token (from @BotFather)
   - `TG_CHAT_ID`: your chat id (message @userinfobot to get it)
   - `HELIUS_KEY`: **recommended**. It's free at <https://dashboard.helius.dev>. Without it,
     the public Solana feed drops every ~40 seconds and can miss trades.
4. Double-click the **PumpDesk** icon. A small console window starts the engine
   (close it to stop), and the dashboard opens in its own app window.
5. In the dashboard, open **Settings & export**, then **Send Telegram test**.

## What you see

| Tab | What's there |
|---|---|
| **Live** | Open paper positions with live P&L, tokens being watched with the reason each isn't a buy yet, and every PASS/FAIL decision |
| **Journal** | Equity curve, P&L by hour, exit reasons, top reject reasons, closed trades |
| **Rules** | `rules.yaml`, your preference memory. Edit it and click Save; changes apply instantly |
| **Settings & export** | Download `trades.csv` / `tokens.csv` for your Claude TRADING DESK project, test Telegram, reset the paper account |

All data lives in the `data\` folder: `rules.yaml`, `trades.csv`, `tokens.csv`,
`state.json` and `pumpdesk.log`.

## How a token is judged (defaults = the TRADING DESK rules)

Rejected for good:
- dev sold within 10 min of launch
- more than 3 wallets bought in the launch slot (bundle)
- non-standard curve (mayhem or custom curves, about half of all pump.fun activity, can't be simulated reliably)

Waits and re-checks on every trade:
- curve below 15%
- top 10 holders above 25% of supply
- dev above 5%

Expired: curve above 80%, or token older than 6h. Quiet hours (01:00 to 06:00 local): passes are logged but not entered.

## How the paper fills work (deliberately pessimistic)

- Orders fill **after your latency** (default 600 ms), at the price at that later moment.
  You don't get the price you saw.
- Prices come from the real bonding-curve math. It reproduces actual on-chain fills to
  within 1 part per million (see `tests/test_curve.py`).
- Costs per trade: pump.fun fees from the live events (currently 0.95% + 0.30% creator),
  plus 1% extra slippage, plus 0.0005 SOL per transaction.
- Exits: 50% off at 2x, stop at -35%, breakeven stop after the take-profit, 60 min time stop,
  stale exit after 10 min with no trades, close on graduation.

## When is it OK to go live? (go/no-go)

Don't consider real money until **all** of these hold on paper:
1. **At least 100 closed trades.** Fewer is noise.
2. **Profitable after costs**, and still profitable if you remove the single best trade.
3. **Profitable in at least 3 of 4 separate weeks** (judge the worst week, not the average).
4. **You changed rules fewer than about 10 times.** Every tweak is another trial, and enough
   tweaks will make noise look like an edge.

If it fails these tests, PumpDesk has done its job: it saved you real money.

## Developers

```
python -m venv .venv && .venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m pumpdesk            # add --no-window for headless
```
