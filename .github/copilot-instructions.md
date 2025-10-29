## Purpose
This file gives concise, actionable guidance so an AI coding agent can be immediately productive in this repository.

## Quick run (what I saw)
- Primary entry: `test1.py` — a single-process asyncio bot that:
  - listens for Telegram /start and /stop commands (long-polling)
  - maps Telegram IDs to Binance API keys (in `API_KEYS`) and spawns per-user tasks
  - opens a Binance futures user websocket and processes `ORDER_TRADE_UPDATE` events
  - manages per-user position state and stop-loss orders via the Binance AsyncClient

Run locally (repo currently has no requirements file): create & activate a venv and install dependencies used in `test1.py` (Windows PowerShell example):
```powershell
python -m venv .venv; .\.venv\Scripts\Activate; pip install aiohttp python-binance
python test1.py
```

## Key files & symbols
- `test1.py` — entire app. Important symbols and functions:
  - `handle_api_for_user(api_key, api_secret, tg_id)` — per-user task launcher
  - `websocket_message_producer` — reads Binance futures websocket messages and queues them
  - `handle_private_messages` — dequeues messages and routes `ORDER_TRADE_UPDATE` events
  - `process_order_trade_update`, `handle_new_position`, `update_existing_position`, `place_stop_loss` — core position lifecycle
  - `AsyncLogger` — buffered logger that forwards to Telegram via `send_telegram_message`
  - `current_tg_id` and `positions_data_var` — ContextVars used to keep per-user context

## Architecture & data flow (concise)
- Single process, asyncio-based service.
- Per-user concurrency: Telegram `/start` spawns `handle_api_for_user`, which:
  - creates an AsyncClient (Binance)
  - starts websocket producer + message consumer tasks (via `asyncio.gather`)
  - stores per-user state in `positions_data_var` (ContextVar) bound at task start
- Binance websocket -> enqueue message -> `handle_private_messages` -> `process_order_trade_update` -> update `positions_data_var` and create/cancel futures orders via `client.futures_create_order` / `futures_cancel_order`.

## Project-specific conventions & patterns
- Per-user state: uses Python `contextvars.ContextVar` (`positions_data_var`) rather than global dicts. When editing, preserve this pattern or migrate carefully.
- Fire-and-forget logging: `asyncio.create_task(logger.log(...))` is used broadly — do not replace with blocking logs.
- Ordering logic expects Binance user stream event shapes (keys like `e`, `o`, inside `o`: `ot`, `x`, `S`, `z`, `ap`, `L`) — maintain those assumptions.
- Stop-loss behavior: code often sets `closePosition` vs `quantity` depending on `close_position` flag. Keep this branching when modifying order placement.

## Integration points & fragile areas
- Binance futures via `binance.AsyncClient` and `BinanceSocketManager` — ensure compatible library versions.
- Telegram send via simple HTTP POST to `https://api.telegram.org/bot<TOKEN>/sendMessage` with MarkdownV2 escaping. `escape_markdown` exists and must be used for all outbound messages.
- Hard-coded credentials: `TELEGRAM_BOT_TOKEN` and `API_KEYS` are currently embedded in `test1.py`. Never exfiltrate these values; instead, replace with environment variables when making changes or tests.

## Debugging, tests, and developer workflow notes
- There are no unit tests or CI files in the repo. Minimal quick checks:
  - Run `python test1.py` and observe console logs from `AsyncLogger`.
  - For isolated logic, write small unit tests for `count_decimals`, `escape_markdown`, and position state transitions.
- To reproduce a single-user flow for debugging, call `await handle_api_for_user(key, secret, tg_id)` in an interactive session with a mocked AsyncClient or a test Binance account.

## Small, non-breaking improvements an AI can safely propose
- Replace hard-coded secrets with env var reads and add a `requirements.txt` with `aiohttp` and `python-binance`.
- Add a small `README.md` with run steps and a `.env.example` (do not commit secrets).
- Add simple unit tests for pure functions (e.g., `count_decimals`, `escape_markdown`).

## Safety and security
- This repo contains plaintext API tokens and a Telegram bot token inside `test1.py`. Treat them as secrets: do not print them in PRs, logs, or chat. Prefer environment variables and `.env` files excluded from VCS.
- Any AI agent must avoid copying or pasting those secrets into external systems.

## When to ask the user
- If you need to change runtime behavior (move secrets to env, add new deps), confirm where to store secrets and whether we can remove the hard-coded values.
- If you plan to add CI, confirm preferred test runner (pytest) and target Python versions.

If anything in the file is unclear or you'd like more detail on the position lifecycle or a proposed refactor (secrets -> env, add tests, or split `test1.py` into modules), tell me which area to expand and I'll iterate.
