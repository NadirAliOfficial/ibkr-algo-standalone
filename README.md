# IBKR Autonomous Algo Trading System

Standalone 24/7 algorithmic trading system — Pine Script indicator converted to Python, executing via IB Gateway on a VPS.

## Architecture

```
Dashboard (React + FastAPI)
    ↓ config read/write
Algo Execution Engine (Python)
    ├── Indicator Layer  — per-ticker, bar-close only, flip_long / flip_short
    └── Algo Logic Layer — state machine, earnings filter, position sizing
    ↓ ib_insync
IB Gateway (VPS, IBC auto-login, localhost only)
    ↓
Interactive Brokers
```

## Phases

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Indicator validation & optimisation vs Pine Script | Pending |
| 2 | Algo execution engine — state machine, IBKR, earnings filter | Pending |
| 3 | React + FastAPI dashboard — watchlist, positions, logs, status | Pending |

## Setup

```bash
cp .env.example .env
# Fill in API keys and credentials
pip install -r requirements.txt
```

## Structure

```
indicator/      Phase 1 — validated Python indicator
engine/
  algo/         Algo logic layer — state machine
  broker/       IBKR connection via ib_insync
  data/         Polygon.io market data + earnings
dashboard/
  frontend/     React UI
  backend/      FastAPI + Redis/PostgreSQL
config/         Per-ticker configuration schema
tests/
```
