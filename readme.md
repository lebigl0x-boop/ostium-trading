Date: January 03, 2026
Version: v2.0 – Full refactor from scratch based on analysis of https://github.com/lebigl0x-boop/ostium-trading
Author: Grok (built by xAI) – Expert in Python DeFi bots, Ostium SDK, Telegram async, GraphQL subgraphs
This document is a complete blueprint to rebuild your project from zero. It incorporates ALL features from your current repo (based on full code analysis from raw files), fixes all bugs (e.g., SDK misuse, async warnings, wrong pair mapping), and adds robustness (error retries, tests, modular structure).
The goal is a clean, scalable bot that:

Monitors multiple traders' positions via subgraph polling.
Calculates unrealized drawdown (per position or pair, configurable thresholds).
Alerts via Telegram on new positions or drawdown triggers.
Copies trades (long/short, market order) with configurable amount/leverage/slippage.
Automatically calculates and sets TP/SL based on PnL % targets.
Handles USDC approval if needed.
Supports TEST_MODE for simulation.
Uses async everywhere for efficiency (no blocking).

Why refactor from scratch?
Your current code has accumulated issues: sync/async mix (causing "never awaited" warnings), wrong SDK calls (e.g., treating perform_trade as SignedTransaction, non-existent 'trading' attribute), hardcodes (e.g., BTC-USD for pair=8 instead of XAG-USD), incomplete commands (e.g., /set_drawdown), no retries on RPC/subgraph fails, basic logging, no tests. Starting clean ensures reliability.
Full Feature List (from Current Repo + Improvements)
All features from your repo are preserved and enhanced:

Monitoring & Polling:
Poll subgraph for open positions of multiple traders (from config.json "traders" list).
Fetch markets/pairs dynamically (cache for speed).
Correct pair_index → symbol mapping (e.g., 0=BTC-USD, 8=XAG-USD).
Polling interval configurable (POLL_INTERVAL_SECONDS in .env).

Drawdown Calculation:
Unrealized drawdown % per position (includes leverage) or per pair.
Configurable mode ("position" or "paire"), min/max thresholds (drawdown_min/max in config.json).
Uses vault contract for position data, public API for current prices.
Trigger alerts if drawdown in [min, max] range.

Telegram Bot:
Commands: /start (menu), /positions (list open positions with copy buttons), /wallet (alias for /positions), /set_drawdown (change thresholds).
Menus: Inline keyboards for config changes (drawdown, amount, leverage, add/remove/list traders).
Presets for quick changes (e.g., drawdown 10-20%, amount 50/100 USDC).
Callbacks for copy buttons (e.g., "Copy LONG" triggers trade).
Restricted to TELEGRAM_CHAT_ID.
Async handlers to fix "never awaited" warnings.

Copy-Trading:
Open market trade copying direction/long-short.
Configurable: amount_in (USDC), leverage (x), slippage_bps (basis points).
Auto-calculate TP/SL prices based on entry price, leverage, tp_pnl_targets ([%] list), sl_pnl (negative %).
Include TP/SL in trade params if "copy_tp_sl": true in config.json.
Check USDC balance/allowance; auto-approve unlimited if needed (skip in TEST_MODE).
TEST_MODE: Simulate without tx (log params/receipt sim).
Proportionnel option (future: % of target size based on your balance vs target).

Config Management:
.env: RPC, keys, addresses, intervals, thresholds, Telegram, TEST_MODE, LOG_LEVEL.
config.json: drawdown_min/max/mode, amount_in, leverage, tp_pnl_targets, sl_pnl, slippage_bps, traders (list), copy_tp_sl (bool).
Validation: Ensure positive amounts/leverage >1, valid addresses, save changes via Telegram.

Error Handling & Robustness:
Retries on RPC/subgraph fails (tenacity library).
Logging: Console + file rotation, levels (DEBUG/INFO/ERROR).
Graceful errors: Send Telegram alerts on failures.
Security: No key hardcodes, .env ignored.

Other:
USDC decimals/precision handled (6 decimals, 1e18 prices).
Arbiscan API for ABI if needed (optional).
Tests: Pytest for key functions (drawdown calc, TP/SL, simulation).


Project Structure
Minimal and modular:
textostium-trading/
├── main.py                 # Entry point: async loop for polling + Telegram bot
├── config.py               # Load/validate .env + config.json (use pydantic)
├── subgraph.py             # Async GraphQL queries (gql + aiohttp): get_positions, get_pairs
├── trading.py              # SDK init, approve USDC, open_trade (with TP/SL), simulate in TEST_MODE
├── alerts.py               # Async Telegram functions: send_message, edit_message, menus/buttons
├── logging_config.py       # Setup logging (console + file)
├── .env                    # Private keys/RPC (gitignore)
├── config.json             # Drawdown/trade params (gitignore if sensitive)
├── env.example             # Template .env
├── requirements.txt        # Dependencies
├── tests/                  # Pytest files
│   └── test_trading.py     # Example tests
├── README.md               # This file
└── .gitignore              # .env, .venv, __pycache__, logs
.gitignore (copy-paste):
text.venv
__pycache__
*.pyc
.env
config.json  # If sensitive; else remove this line
logs/
.pytest_cache
env.example (expanded from your repo):
text# Arbitrum RPC (Alchemy/Infura recommended for prod)
ARBITRUM_RPC_URL=https://arb1.arbitrum.io/rpc

# Target traders (comma-separated if multiple; but use config.json "traders" list)
TARGET_WALLET=0x3a17bDE82706aaF297A0d721D5c31E4EB3c48B65

# Ostium endpoints
OSTIUM_SUBGRAPH_URL=https://api.thegraph.com/subgraphs/name/ostium-labs/ostium-arbitrum
OSTIUM_VAULT_ADDRESS=0xYourVaultAddress
OSTIUM_ROUTER_ADDRESS=0x6d0bA1f9996DBD8885827e1b2e8f6593e7702411
USDC_ADDRESS=0xaf88d065e77c8cC2239327C5EDb3A432268e5831

# Drawdown thresholds (overrides for initial; use config.json)
DRAWDOWN_THRESHOLD_MIN=20.0
DRAWDOWN_THRESHOLD_MAX=30.0

# Polling
POLL_INTERVAL_SECONDS=30

# Precisions
PRICE_PRECISION=1000000000000000000000000000000  # 1e30, adjust if needed
USD_PRECISION=1000000000000000000000000000000   # 1e30
USDC_DECIMALS=1000000                            # 1e6

# Telegram
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id

# Arbiscan (optional for ABI fetch)
ARBISCAN_API_KEY=your_key

# Copy-trade wallet
PRIVATE_KEY=0x...
WALLET_ADDRESS=0x...

# Modes
TEST_MODE=true
LOG_LEVEL=INFO
requirements.txt (updated from yours):
textweb3>=6.15.0,<7.0.0
requests>=2.32.0,<3.0.0
colorama>=0.4.6,<1.0.0
ostium-python-sdk>=3.0.0
python-dotenv>=1.0.1,<2.0.0
python-telegram-bot>=21.4,<22.0
gql[aiohttp]>=3.5.0,<4.0.0
tenacity>=8.2.0,<9.0.0
pydantic>=2.0.0,<3.0.0
pytest>=8.0.0,<9.0.0
certifi>=2024.0.0  # For SSL fixes