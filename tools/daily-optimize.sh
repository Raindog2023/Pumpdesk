#!/bin/bash
# Daily optimizer: runs backtest sweep, auto-applies if Sharpe > threshold.
# Scheduled via cron (cronjob_manage). Output is delivered to Telegram.
cd /opt/data/projects/pumpdesk
set -a; source .env 2>/dev/null; set +a  # load TG_TOKEN, TG_CHAT_ID, HELIUS_KEY
export PYTHONPATH="/opt/data/projects/pumpdesk:$PYTHONPATH"

# --- Run optimizer in dry-run mode ---
RESULTS=$(.venv/bin/python tools/optimize_rules.py \
    --phase combined --top 5 --min-val-trades 20 2>&1)

echo "$RESULTS"
echo "==="

# --- Extract best validation Sharpe from the top-ranked combo ---
BEST_SH=$(echo "$RESULTS" | grep -oP '^⭐\d+\s+\K[+-]?\d+\.\d+' | head -1)
BEST_PARAMS=$(echo "$RESULTS" \
    | grep -oP 'curve_max_pct:\d+ curve_min_pct:\d+ max_block0_buyers:\d+ max_dev_pct:\d+ max_top10_pct:\d+ stop_loss_pct:\d+ take_profit_multiple:\d+\.?\d* take_profit_sell_pct:\d+' \
    | head -1)

echo "Best validation Sharpe: ${BEST_SH:-none}"
echo "Best params: ${BEST_PARAMS:-none}"
echo "==="

# --- Auto-apply if Sharpe is healthy (> 0.3) ---
THRESHOLD=0.3
if [ -n "$BEST_SH" ] && python3 -c "import sys; sys.exit(0 if float('$BEST_SH') > $THRESHOLD else 1)"; then
    echo "✅ Sharpe $BEST_SH > $THRESHOLD — applying best parameters to rules.yaml"
    .venv/bin/python tools/optimize_rules.py \
        --phase combined --top 1 --min-val-trades 20 --apply --notify 2>&1
else
    echo "⏸  Sharpe ${BEST_SH:-N/A} <= $THRESHOLD — not applying (review manually with --apply)"
fi
