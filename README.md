# IBKR Autonomous Algo Trading System

A fully autonomous 24/7 algorithmic trading system powered by the ERGA-SS (Efficiency Ratio SuperSmoother Adaptive) indicator. Runs on a VPS, auto-logs into IB Gateway via IBC, and executes long/short equity trades across up to 50 US stocks with zero manual intervention.

---

## Features

- **ERGA-SS indicator** — Python port of the TradingView Pine Script version, session-aligned bar construction verified to 98-99% bar-by-bar match
- **Autonomous execution** — connects to IB Gateway on startup, syncs positions, and runs a 1-second evaluation loop aligned to candle closes
- **Multi-timeframe** — 1m, 5m, 15m, 30m, 1H, 2H, 3H, 4H, 1D; 2H/3H/4H built from 30m bars anchored to 09:30 ET session open (matches TradingView exactly)
- **Earnings protection** — auto-closes positions and blocks signals within 3 days of earnings
- **Hot-reload** — timeframe and indicator param changes from the dashboard apply on the next candle cycle, no restart needed
- **Redis log persistence** — signal, trade, and earnings logs survive process restarts
- **VPS deployment** — one-command setup script with nginx, HTTPS, Redis, systemd, and IBC headless auto-login

---

## Architecture

```
Browser
  └── React Dashboard  ──────────────────────── http(s)://your-vps
        └── FastAPI Backend (port 8000)
              ├── /api/tickers      watchlist CRUD, hot-reload params
              ├── /api/positions    live IBKR positions + P&L
              ├── /api/logs         signals, trades, earnings (Redis-backed)
              └── /api/system       engine status, halt management

AlgoRunner  (main thread, 1s cycle)
  ├── PolygonData          OHLCV candles, ET-aligned 2H/3H/4H resampling
  ├── ERGAIndicator        stateful per-ticker, candle-close aligned
  ├── SignalProcessor      close existing → open new, confirmed fills only
  ├── PositionStateMachine IBKR-synced flat/long/short state per ticker
  └── EarningsFilter       auto-close + signal block around earnings

IBKRConnection  (ib_insync)
  └── IB Gateway  ←── IBC headless auto-login (paper or live)
        └── Interactive Brokers
```

---

## Phases

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | ERGA-SS Python port, TradingView alignment validation | Done |
| 2 | Execution engine — Polygon data, IBKR orders, earnings filter | Done |
| 3 | React dashboard, VPS deployment, IBC auto-login, Redis persistence | Done |

---

## Quick Start (Local)

**Prerequisites:** IB Gateway running on port 4002 (paper) or 4001 (live), Python 3.10+, Node 18+

```bash
git clone https://github.com/NadirAliOfficial/ibkr-algo-standalone.git
cd ibkr-algo-standalone

# Python dependencies
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Build frontend
cd dashboard/frontend && npm install && npm run build && cd ../..

# Configure
cp .env.example .env
# Edit .env — set POLYGON_API_KEY, IBKR_PORT, DASHBOARD_PASSWORD

# Run
python main.py
```

Dashboard at `http://localhost:8000`

---

## VPS Deployment (Ubuntu 22.04 / 24.04)

```bash
# On your VPS as root:
git clone https://github.com/NadirAliOfficial/ibkr-algo-standalone.git /opt/ibkr-algo
bash /opt/ibkr-algo/deploy/setup.sh your-domain.com
```

The setup script installs and configures:
- Python venv + dependencies
- Node + React frontend build
- Redis (log persistence)
- nginx with HTTPS (Let's Encrypt)
- systemd services for the dashboard and trading bot (auto-restart on failure/reboot)

Edit `/opt/ibkr-algo/.env` after setup to add your credentials.

**IB Gateway headless login (IBC):**

```bash
# Download IB Gateway stable installer and install to /opt/ibgateway
# Download IBC to /opt/ibc
# Edit /root/ibc/config.ini — set IbLoginId, IbPassword, TradingMode
# Start:
Xvfb :2 -screen 0 1024x768x24 &
bash /root/start_gw.sh
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `IBKR_HOST` | `127.0.0.1` | IB Gateway host |
| `IBKR_PORT` | `4002` | 4002 = paper, 4001 = live |
| `IBKR_CLIENT_ID` | `10` | Must be unique per connected client |
| `POLYGON_API_KEY` | — | [Polygon.io](https://polygon.io) API key |
| `DASHBOARD_USERNAME` | `admin` | Dashboard login username |
| `DASHBOARD_PASSWORD` | `changeme` | Dashboard login password |
| `DASHBOARD_HOST` | `0.0.0.0` | Bind host for uvicorn |
| `DASHBOARD_PORT` | `8000` | Dashboard port |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis URL (logs fall back to in-memory if unavailable) |
| `LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING |

---

## Project Structure

```
main.py                         entry point, wires all components
indicator/
  erga_ss_lib.py                ERGA-SS core (SuperSmoother, ADX, regime gate)
  erga_indicator.py             stateful wrapper, bar deduplication, confirmation logic
  signal.py                     Signal dataclass
engine/
  broker/connection.py          IBKRConnection — order placement, fill polling, reconnect
  data/polygon.py               OHLCV fetcher, 30m-based ET-aligned resampling (2H/3H/4H)
  data/earnings.py              earnings check via Polygon + FMP fallback
  log_store.py                  Redis-backed log store with in-memory fallback
  algo/state_machine.py         per-ticker flat/long/short state, IBKR sync
  algo/signal_processor.py      close existing → open new, halt on fill failure
  algo/earnings_filter.py       auto-close positions before earnings
  runner.py                     main loop, candle-close timing, hot-reload
config/
  schema.py                     TickerConfig dataclass
  store.py                      thread-safe JSON config store
  tickers.json                  active ticker configs (managed via dashboard)
dashboard/
  backend/main.py               FastAPI app, Basic auth, static file serving
  backend/routers/              tickers, positions, logs, system endpoints
  frontend/src/App.jsx          React dashboard (login, watchlist, positions, logs)
deploy/
  setup.sh                      one-command VPS setup (Ubuntu)
  nginx.conf                    HTTPS reverse proxy config
  ibkr-dashboard.service        systemd unit for FastAPI
  ibkr-bot.service              systemd unit for trading engine
tests/                          unit tests — indicator, state machine, signal flow
```

---

## ERGA-SS Indicator

ERGA-SS adapts its smoothing period based on market efficiency (Kaufman's Efficiency Ratio). In trending conditions it uses a shorter period (fast response); in choppy conditions it extends the period (noise rejection).

**Default parameters:**

| Parameter | Value | Description |
|-----------|-------|-------------|
| `slow_len` | 50 | Slow SuperSmoother period |
| `fast_len` | 15 | Fast SuperSmoother period |
| `er_len` | 20 | Efficiency Ratio lookback |
| `adx_len` | 14 | ADX trend filter period |
| `min_quality` | 25.0 | Minimum trend quality gate |
| `calc_mode` | Adaptive | Adaptive / Fixed |

Signals fire on the first confirmed bar after a SuperSmoother crossover passes the ADX quality gate.

---

## Bar Construction

Multi-hour bars (2H, 3H, 4H) are built from 30-minute Polygon bars aggregated using **09:30 ET session-open anchoring** — the same method TradingView uses. This ensures bar boundaries (e.g. 3H = 09:30, 12:30, 15:30 ET) match TradingView exactly, preventing signal timing offsets.

---

## Running Tests

```bash
python -m pytest tests/ -v
```

---

## License

MIT
<!-- updated: 2024-03-15-r01 -->
