# IBKR Autonomous Algo Trading System

Standalone 24/7 algorithmic trading system powered by the ERGA-SS indicator. Runs on a VPS, connects to IB Gateway, and executes fully autonomous long/short equity trades across up to 50 US stocks.

## Phases

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | ERGA-SS indicator — Python port, TradingView alignment validation | Done (`v1.0-phase1`) |
| 2 | Execution engine — Polygon data, IBKR orders, earnings filter, dashboard | Done (`v2.0-phase2`) |
| 3 | VPS deployment, IBC auto-login, live trading hardening | Planned |

## Architecture

```
browser
  └── React Dashboard (port 8000)
        └── FastAPI Backend
              ├── /api/tickers     — watchlist CRUD + hot-reload
              ├── /api/positions   — live IBKR positions
              ├── /api/logs        — signals, trades, earnings
              └── /api/system      — engine status, halt management

FastAPI Backend (same process)
  └── AlgoRunner (main thread, 1s cycle)
        ├── ERGAIndicator per ticker   — candle-close aligned evaluation
        ├── SignalProcessor            — close → open with confirmed fills
        ├── PositionStateMachine       — IBKR-synced state per ticker
        └── EarningsFilter             — auto-close + signal block

AlgoRunner
  └── IBKRConnection (ib_insync)
        └── IB Gateway → Interactive Brokers
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt
cd dashboard/frontend && npm install && npm run build && cd ../..

# 2. Configure
cp .env.example .env
# Set POLYGON_API_KEY, IBKR_PORT (4002=paper, 4001=live), DASHBOARD_PASSWORD

# 3. Start IB Gateway, then run
python main.py
```

Dashboard opens at `http://localhost:8000` (default: admin / no password).

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `IBKR_HOST` | `127.0.0.1` | IB Gateway host |
| `IBKR_PORT` | `4002` | 4002 = paper, 4001 = live |
| `IBKR_CLIENT_ID` | `10` | Must be unique per connected client |
| `POLYGON_API_KEY` | — | Polygon.io API key (market data) |
| `DASHBOARD_USERNAME` | `admin` | Dashboard login |
| `DASHBOARD_PASSWORD` | `changeme` | Dashboard password |
| `DASHBOARD_PORT` | `8000` | Dashboard port |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING |

## Project Structure

```
main.py                    — entry point, wires everything together
indicator/
  erga_ss_lib.py           — ERGA-SS core (SuperSmoother, ADX, regime gate)
  erga_indicator.py        — stateful wrapper, bar deduplication
  signal.py                — Signal dataclass
engine/
  broker/connection.py     — IBKRConnection, order placement, fill polling
  data/polygon.py          — OHLCV fetcher, ET-aligned 2H/3H/4H resampling
  data/earnings.py         — earnings check via Polygon + FMP fallback
  algo/state_machine.py    — per-ticker flat/long/short state, IBKR sync
  algo/signal_processor.py — close existing → open new, halt on failure
  algo/earnings_filter.py  — auto-close positions before earnings
  runner.py                — main loop, candle-close timing, hot-reload
config/
  schema.py                — TickerConfig dataclass
  store.py                 — thread-safe JSON config store
  tickers.json             — active ticker configs (edited via dashboard)
dashboard/
  backend/main.py          — FastAPI app, auth, static file serving
  backend/routers/         — tickers, positions, logs, system
  frontend/src/            — React + Tailwind dashboard
tests/                     — 16 unit tests (indicator, state machine)
```

## Key Design Decisions

**Candle-close aligned evaluation** — bars are evaluated within 30 seconds of their expected close time (ET session-aligned, matching TradingView). Never acts on a forming bar.

**ET-aligned bar resampling** — 2H, 3H, 4H bars are reconstructed from 1H Polygon data using 09:30 ET session boundaries, matching TradingView exactly.

**Confirmed fills only** — every order polls fill status every 0.5s up to 30s before proceeding. A failed close aborts the sequence and halts the ticker rather than opening a position with unknown state.

**Hot-reload** — timeframe or param changes made in the dashboard take effect on the next candle cycle without restarting the engine.

**Earnings protection** — positions are auto-closed and new signals blocked within a configurable window (default 3 days) around earnings dates.

## Running Tests

```bash
python -m pytest tests/ -v
```

16 tests covering indicator evaluation, signal deduplication, state machine transitions, and halt logic.
