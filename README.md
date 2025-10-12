# IBKR Autonomous Algo Trading System

A fully autonomous 24/7 algorithmic trading system powered by the ERGA-SS (Efficiency Ratio SuperSmoother Adaptive) indicator. Designed to run on a VPS with auto-login via IBC, executing long/short equity trades across up to 50 US stocks with zero manual intervention.

![Python](https://img.shields.io/badge/Python-3.9+-3776AB?style=flat&logo=python&logoColor=white)
![IBKR](https://img.shields.io/badge/Interactive%20Brokers-TWS%20API-red?style=flat)

## Strategy — ERGA-SS

The ERGA-SS indicator adapts its smoothing period based on the Efficiency Ratio of price movement:
- High efficiency (trending) → shorter smoothing → faster signals
- Low efficiency (choppy) → longer smoothing → filters noise

Trades are taken when the adaptive smoother crosses its signal line, confirmed by a minimum efficiency ratio threshold.

## System Modules

| File                    | Purpose                                           |
|-------------------------|---------------------------------------------------|
| `main.py`               | Entry point — connects to IBKR, runs trading loop |
| `export_signals.py`     | Export ERGA-SS signals to CSV                     |
| `export_signals_3h.py`  | 3-hour timeframe signal export                    |
| `generate_charts.py`    | Equity curve and signal visualization             |
| `align_validate.py`     | Signal alignment validation across timeframes     |
| `validate_phase1.py`    | Phase 1 backtest validation suite                 |

## Setup

1. Install IB Gateway + IBC for headless auto-login
2. Install dependencies:

```bash
pip install ib_insync pandas numpy matplotlib
```

3. Configure your account and stock universe in `main.py`

4. Run:

```bash
python main.py
```

## Features

- Fully autonomous — no manual input required once running
- Trades up to 50 US equities simultaneously
- Long and short positions supported
- CSV signal export for analysis and backtesting
- Chart generation for performance review
- Auto-reconnect on connection drop

## License

MIT

