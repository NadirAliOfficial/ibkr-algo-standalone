"""
Signal report for Richard: ORCL, QCOM, ARM, META, TSLA, PLTR on 2H/3H/4H (6 months).
Also includes original 1H tickers.
"""

import csv
import logging
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.WARNING)

import pandas as pd
from engine.data.polygon import PolygonData
from indicator.erga_indicator import ERGAIndicator

ET = ZoneInfo("America/New_York")
MARKET_OPEN  = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)

TICKERS_CONFIG = [
    {"ticker": "ORCL",  "timeframe": "3H", "params": {}},
    {"ticker": "QCOM",  "timeframe": "3H", "params": {}},
    {"ticker": "ARM",   "timeframe": "3H", "params": {}},
    {"ticker": "META",  "timeframe": "3H", "params": {}},
    {"ticker": "TSLA",  "timeframe": "3H", "params": {}},
    {"ticker": "PLTR",  "timeframe": "3H", "params": {}},
    {"ticker": "ORCL",  "timeframe": "2H", "params": {}},
    {"ticker": "QCOM",  "timeframe": "2H", "params": {}},
    {"ticker": "ARM",   "timeframe": "2H", "params": {}},
    {"ticker": "META",  "timeframe": "2H", "params": {}},
    {"ticker": "TSLA",  "timeframe": "2H", "params": {}},
    {"ticker": "PLTR",  "timeframe": "2H", "params": {}},
    {"ticker": "ORCL",  "timeframe": "4H", "params": {}},
    {"ticker": "QCOM",  "timeframe": "4H", "params": {}},
    {"ticker": "ARM",   "timeframe": "4H", "params": {}},
    {"ticker": "META",  "timeframe": "4H", "params": {}},
    {"ticker": "TSLA",  "timeframe": "4H", "params": {}},
    {"ticker": "PLTR",  "timeframe": "4H", "params": {}},
    {"ticker": "AAPL",  "timeframe": "1H", "params": {"slow_len": 30, "fast_len": 10}},
    {"ticker": "TSLA",  "timeframe": "1H", "params": {}},
    {"ticker": "NVDA",  "timeframe": "1H", "params": {}},
    {"ticker": "MSFT",  "timeframe": "1H", "params": {}},
    {"ticker": "AMZN",  "timeframe": "1H", "params": {}},
]

CUTOFF = (datetime.now(ET) - timedelta(days=182)).strftime("%Y-%m-%d")


def filter_rth(candles: pd.DataFrame) -> pd.DataFrame:
    et_index = candles.index.tz_convert(ET)
    mask = (
        (et_index.weekday < 5) &
        (et_index.time >= MARKET_OPEN) &
        (et_index.time <= MARKET_CLOSE)
    )
    return candles[mask]


def run_indicator_rth(ticker, timeframe, candles, params):
    rth = filter_rth(candles)
    if rth.empty:
        return []

    ind = ERGAIndicator(ticker=ticker, timeframe=timeframe, params=params)
    signals = []

    for i in range(1, len(rth) + 1):
        signal = ind.evaluate(rth.iloc[:i])
        if signal is not None:
            ts = pd.Timestamp(signal.bar_time)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            bar_et = ts.astimezone(ET)
            signals.append({
                "ticker":        ticker,
                "timeframe":     timeframe,
                "bar_close_utc": ts.strftime("%Y-%m-%d %H:%M UTC"),
                "bar_close_est": bar_et.strftime("%Y-%m-%d %H:%M ET"),
                "signal":        "LONG" if signal.signal_type.value == "flip_long" else "SHORT",
                "close_price":   round(float(signal.close_price), 4),
            })

    return signals


def main():
    polygon = PolygonData()
    all_signals = []

    for cfg in TICKERS_CONFIG:
        ticker = cfg["ticker"]
        tf = cfg["timeframe"]
        key = f"{ticker} {tf}"
        print(f"Fetching {key}...")

        candles = polygon.fetch_candles(ticker, tf, warmup_bars=600)
        if candles is None or candles.empty:
            print(f"  WARNING: no candles for {key}")
            continue

        rth_count = len(filter_rth(candles))
        print(f"  {len(candles)} total candles, {rth_count} RTH bars")

        sigs = run_indicator_rth(ticker, tf, candles, cfg["params"])
        print(f"  {len(sigs)} crossovers found")
        all_signals.extend(sigs)

    # Sort descending, keep last 6 months
    all_signals.sort(key=lambda x: x["bar_close_utc"], reverse=True)
    all_signals = [s for s in all_signals if s["bar_close_utc"] >= CUTOFF]

    print("\n=== Validation ===")
    from collections import defaultdict
    by_key = defaultdict(list)
    for s in all_signals:
        by_key[f"{s['ticker']}_{s['timeframe']}"].append(s)
    for key, sigs in by_key.items():
        sigs_asc = sorted(sigs, key=lambda x: x["bar_close_utc"])
        issues = [i for i in range(1, len(sigs_asc)) if sigs_asc[i]["signal"] == sigs_asc[i-1]["signal"]]
        if issues:
            for i in issues:
                print(f"  WARN: {key} consecutive {sigs_asc[i]['signal']} at {sigs_asc[i-1]['bar_close_est']} -> {sigs_asc[i]['bar_close_est']}")
        else:
            print(f"  {key}: OK ({len(sigs_asc)} signals)")

    with open("signal_report_3h.csv", "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["ticker", "timeframe", "bar_close_utc", "bar_close_est", "signal", "close_price"])
        writer.writeheader()
        writer.writerows(all_signals)
    print(f"\nCSV: signal_report_3h.csv ({len(all_signals)} signals)")

    rows_html = ""
    for s in all_signals:
        color = "#22c55e" if s["signal"] == "LONG" else "#ef4444"
        rows_html += f"""
        <tr>
          <td>{s['ticker']}</td>
          <td>{s['timeframe']}</td>
          <td>{s['bar_close_utc']}</td>
          <td>{s['bar_close_est']}</td>
          <td style="color:{color};font-weight:bold">{s['signal']}</td>
          <td>${s['close_price']}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>ERGA-SS Signal Report - Multi-Timeframe</title>
<style>
  body {{ font-family: sans-serif; background: #0f172a; color: #e2e8f0; padding: 24px; }}
  h1 {{ color: #fff; font-size: 20px; margin-bottom: 4px; }}
  p {{ color: #94a3b8; font-size: 13px; margin-bottom: 20px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th {{ background: #1e293b; color: #94a3b8; padding: 8px 12px; text-align: left; border-bottom: 1px solid #334155; }}
  td {{ padding: 7px 12px; border-bottom: 1px solid #1e293b; }}
  tr:hover td {{ background: #1e293b; }}
</style>
</head>
<body>
<h1>ERGA-SS Signal Report - Multi-Timeframe (RTH Only)</h1>
<p>Generated: {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')} &nbsp;|&nbsp;
{len(all_signals)} signals &nbsp;|&nbsp;
RTH bars only (09:30-16:00 ET) &nbsp;|&nbsp;
3H/2H/4H bars session-aligned to 09:30 ET (matches TradingView exactly)</p>
<table>
  <thead>
    <tr>
      <th>Ticker</th><th>Timeframe</th><th>Bar Close (UTC)</th><th>Bar Close (ET)</th><th>Signal</th><th>Close Price</th>
    </tr>
  </thead>
  <tbody>{rows_html}</tbody>
</table>
</body>
</html>"""

    with open("signal_report_3h.html", "w") as f:
        f.write(html)
    print("HTML: signal_report_3h.html")


if __name__ == "__main__":
    main()
