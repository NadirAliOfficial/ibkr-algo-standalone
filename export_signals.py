"""
Generate historical signal report for client verification.
Fetches candles from Polygon, runs ERGA-SS across all bars,
outputs every crossover with timestamp + price to CSV + HTML.
"""

import os
import sys
import csv
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.WARNING)

import pandas as pd
from engine.data.polygon import PolygonData
from indicator.erga_indicator import ERGAIndicator

ET = ZoneInfo("America/New_York")

TICKERS_CONFIG = [
    {"ticker": "AAPL", "timeframe": "4H", "params": {"slow_len": 30, "fast_len": 10}},
    {"ticker": "TSLA", "timeframe": "1H", "params": {}},
    {"ticker": "NVDA", "timeframe": "1H", "params": {}},
    {"ticker": "MSFT", "timeframe": "1H", "params": {}},
    {"ticker": "AMZN", "timeframe": "1H", "params": {}},
]


def run_indicator_full(ticker, timeframe, candles, params):
    """
    Run the exact same ERGAIndicator logic as the live bot, bar by bar.
    Feeds candles one at a time to match incremental live behaviour.
    """
    ind = ERGAIndicator(ticker=ticker, timeframe=timeframe, params=params)
    signals = []

    # Feed bars one at a time — exact same path as the live engine
    for i in range(1, len(candles) + 1):
        window = candles.iloc[:i]
        signal = ind.evaluate(window)
        if signal is not None:
            bar_time = signal.bar_time
            ts = pd.Timestamp(bar_time)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            bar_et = ts.astimezone(ET)
            signals.append({
                "ticker":        None,
                "timeframe":     None,
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
        print(f"Fetching {ticker} {tf}...")

        candles = polygon.fetch_candles(ticker, tf, warmup_bars=600)
        if candles is None or candles.empty:
            print(f"  WARNING: no candles for {ticker}")
            continue

        print(f"  {len(candles)} candles fetched")
        sigs = run_indicator_full(ticker, tf, candles, cfg["params"])

        for s in sigs:
            s["ticker"] = ticker
            s["timeframe"] = tf
            # Flag whether bot would actually execute (market hours only)
            try:
                from zoneinfo import ZoneInfo
                from datetime import time as dtime
                bar_et_str = s["bar_close_est"]
                bar_time_et = datetime.strptime(bar_et_str, "%Y-%m-%d %H:%M ET")
                t = bar_time_et.time()
                s["executed_by_bot"] = "YES" if dtime(9, 30) <= t <= dtime(16, 0) else "NO (pre/post market)"
            except Exception:
                s["executed_by_bot"] = "UNKNOWN"

        print(f"  {len(sigs)} crossovers found")
        all_signals.extend(sigs)

    # Sort by UTC time descending (most recent first)
    all_signals.sort(key=lambda x: x["bar_close_utc"], reverse=True)

    # Keep last 90 days only
    cutoff = "2026-01-20"
    all_signals = [s for s in all_signals if s["bar_close_utc"] >= cutoff]

    # Write CSV
    csv_path = "signal_report.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["ticker", "timeframe", "bar_close_utc", "bar_close_est", "signal", "close_price", "executed_by_bot"])
        writer.writeheader()
        writer.writerows(all_signals)
    print(f"\nCSV written: {csv_path} ({len(all_signals)} signals)")

    # Write HTML
    html_path = "signal_report.html"
    rows_html = ""
    for s in all_signals:
        color = "#22c55e" if s["signal"] == "LONG" else "#ef4444"
        bot_color = "#94a3b8" if "NO" in s.get("executed_by_bot", "") else "#fff"
        rows_html += f"""
        <tr>
          <td>{s['ticker']}</td>
          <td>{s['timeframe']}</td>
          <td>{s['bar_close_utc']}</td>
          <td>{s['bar_close_est']}</td>
          <td style="color:{color};font-weight:bold">{s['signal']}</td>
          <td>${s['close_price']}</td>
          <td style="color:{bot_color};font-size:11px">{s.get('executed_by_bot','')}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>ERGA-SS Signal Report</title>
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
<h1>ERGA-SS Signal Report</h1>
<p>Generated: {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')} &nbsp;|&nbsp; {len(all_signals)} crossover signals &nbsp;|&nbsp; Tickers: AAPL (4H), TSLA/NVDA/MSFT/AMZN (1H)</p>
<table>
  <thead>
    <tr>
      <th>Ticker</th><th>Timeframe</th><th>Bar Close (UTC)</th><th>Bar Close (ET)</th><th>Signal</th><th>Close Price</th><th>Bot Executed</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>
</body>
</html>"""

    with open(html_path, "w") as f:
        f.write(html)
    print(f"HTML written: {html_path}")
    print("\nHow to verify on TradingView:")
    print("  1. Open the ticker on TradingView at the matching timeframe")
    print("  2. Find the bar at the 'Bar Close (ET)' timestamp")
    print("  3. Confirm the ERGA-SS crossover occurs on that bar")


if __name__ == "__main__":
    main()
