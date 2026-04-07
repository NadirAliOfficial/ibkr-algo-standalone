"""
TradingView vs Python ERGA-SS Alignment Validator
--------------------------------------------------
Reads each TV export CSV (contains Pine Script SS Line + BUY/SELL),
runs Python ERGA-SS on the same OHLCV bars, produces per-file comparison CSV
and a summary HTML report.
"""

import os, json, math
import pandas as pd
from pathlib import Path
from indicator.erga_ss_lib import ERGA_SS

TV_DIR   = Path("docs/tv_exports")
OUT_DIR  = Path("docs/alignment")
OUT_DIR.mkdir(parents=True, exist_ok=True)

PARAMS = dict(
    slow_len=50, fast_len=15, er_len=20, pre_len=5,
    calc_mode="Adaptive", min_quality=25.0, hysteresis=0.1,
    buffer_mult=0.5, min_trend_bars=3, use_hlc3=True,
)

# ── helpers ──────────────────────────────────────────────────────────────────

def parse_tv_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["dt"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.rename(columns={
        "SS Line":    "tv_ss",
        "SS Shifted": "tv_ss_prev",
        "Ride Line":  "tv_ride",
        "BUY":        "tv_buy",
        "SELL":       "tv_sell",
    })
    return df.set_index("dt").sort_index()


def run_python(df: pd.DataFrame):
    ind = ERGA_SS(**PARAMS)
    py_ss, py_buy, py_sell = [], [], []
    for _, row in df.iterrows():
        ind.update(float(row["close"]), float(row["high"]), float(row["low"]))
        py_ss.append(ind.ss_line)
        py_buy.append(1 if ind.bull_flip else 0)
        py_sell.append(1 if ind.bear_flip else 0)
    df = df.copy()
    df["py_ss"]   = py_ss
    df["py_buy"]  = py_buy
    df["py_sell"] = py_sell
    df["ss_diff"] = df["py_ss"] - df["tv_ss"]
    df["ss_pct"]  = (df["ss_diff"] / df["tv_ss"].abs()).where(df["tv_ss"] != 0) * 100
    return df


def flip_rows(df: pd.DataFrame):
    return df[(df["tv_buy"] == 1) | (df["tv_sell"] == 1) |
              (df["py_buy"] == 1) | (df["py_sell"] == 1)].copy()

# ── process each file ─────────────────────────────────────────────────────────

summary = []

for csv_path in sorted(TV_DIR.glob("*.csv")):
    name   = csv_path.stem.strip()        # e.g. "NASDAQ_GOOG_ 240"
    parts  = name.split("_")
    ticker = parts[1]
    tf_min = parts[2].strip()             # e.g. "240"

    df = parse_tv_csv(csv_path)
    df = run_python(df)

    # Save comparison CSV
    out_csv = OUT_DIR / f"{name.replace(' ','_')}_comparison.csv"
    df[["open","high","low","close",
        "tv_ss","py_ss","ss_diff","ss_pct",
        "tv_buy","tv_sell","py_buy","py_sell"]].to_csv(out_csv)

    # Stats
    flips    = flip_rows(df)
    tv_sigs  = int(df["tv_buy"].sum() + df["tv_sell"].sum())
    py_sigs  = int(df["py_buy"].sum() + df["py_sell"].sum())
    matched  = int(((df["tv_buy"] == df["py_buy"]) & (df["tv_sell"] == df["py_sell"])).all())
    max_diff = float(df["ss_diff"].abs().max())
    avg_diff = float(df["ss_diff"].abs().mean())

    # Per-flip table
    flip_rows_list = []
    for ts, row in flips.iterrows():
        tv_sig  = "BUY"  if row["tv_buy"]  else ("SELL" if row["tv_sell"]  else "—")
        py_sig  = "BUY"  if row["py_buy"]  else ("SELL" if row["py_sell"]  else "—")
        match   = "✓" if tv_sig == py_sig else "✗"
        flip_rows_list.append({
            "timestamp": str(ts)[:16],
            "close":     round(row["close"], 2),
            "tv_ss":     round(row["tv_ss"], 4),
            "py_ss":     round(row["py_ss"], 4),
            "ss_diff":   round(row["ss_diff"], 4),
            "tv_signal": tv_sig,
            "py_signal": py_sig,
            "match":     match,
        })

    summary.append({
        "file":      name,
        "ticker":    ticker,
        "tf_min":    tf_min,
        "bars":      len(df),
        "tv_sigs":   tv_sigs,
        "py_sigs":   py_sigs,
        "max_diff":  round(max_diff, 6),
        "avg_diff":  round(avg_diff, 6),
        "flips":     flip_rows_list,
    })

    print(f"  {ticker:6s} {tf_min:4s}min  bars={len(df):4d}  "
          f"tv_sigs={tv_sigs}  py_sigs={py_sigs}  "
          f"max_ss_diff={max_diff:.4f}  avg={avg_diff:.4f}")

# ── HTML report ───────────────────────────────────────────────────────────────

def sig_color(s):
    if s == "BUY":  return "color:#3fb950;font-weight:bold"
    if s == "SELL": return "color:#f85149;font-weight:bold"
    return "color:#8b949e"

rows_html = ""
for s in summary:
    flip_table = ""
    if s["flips"]:
        flip_table = """
        <details><summary style="cursor:pointer;color:#58a6ff">
          Show flip details ({n} events)
        </summary>
        <table style="margin-top:8px">
          <tr><th>Timestamp</th><th>Close</th><th>TV SS</th><th>Py SS</th>
              <th>Diff</th><th>TV Signal</th><th>Py Signal</th><th>Match</th></tr>
        {rows}
        </table></details>""".format(
            n=len(s["flips"]),
            rows="".join(
                f'<tr>'
                f'<td>{r["timestamp"]}</td>'
                f'<td>{r["close"]}</td>'
                f'<td>{r["tv_ss"]}</td>'
                f'<td>{r["py_ss"]}</td>'
                f'<td style="color:{"#f85149" if abs(r["ss_diff"])>0.01 else "#3fb950"}">{r["ss_diff"]}</td>'
                f'<td style="{sig_color(r["tv_signal"])}">{r["tv_signal"]}</td>'
                f'<td style="{sig_color(r["py_signal"])}">{r["py_signal"]}</td>'
                f'<td style="font-size:16px">{r["match"]}</td>'
                f'</tr>'
                for r in s["flips"]
            )
        )

    match_icon = "✓" if s["tv_sigs"] == s["py_sigs"] else "✗"
    diff_color = "#3fb950" if s["max_diff"] < 0.05 else ("#e3b341" if s["max_diff"] < 0.5 else "#f85149")

    rows_html += f"""
    <tr>
      <td>{s["ticker"]}</td>
      <td style="color:#d2a8ff">{s["tf_min"]}m</td>
      <td>{s["bars"]}</td>
      <td>{s["tv_sigs"]}</td>
      <td>{s["py_sigs"]}</td>
      <td style="color:{diff_color}">{s["max_diff"]}</td>
      <td style="color:{diff_color}">{s["avg_diff"]}</td>
      <td style="font-size:16px">{match_icon}</td>
      <td>{flip_table}</td>
    </tr>"""

html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>ERGA-SS Alignment Report</title>
<style>
  body{{font-family:monospace;font-size:13px;background:#0d1117;color:#e6edf3;padding:20px}}
  h2{{color:#58a6ff}} p{{color:#8b949e}}
  table{{border-collapse:collapse;width:100%}}
  th{{background:#161b22;color:#58a6ff;padding:8px 12px;text-align:left;position:sticky;top:0}}
  td{{padding:6px 12px;border-bottom:1px solid #21262d;vertical-align:top}}
  tr:hover td{{background:#161b22}}
  details table{{background:#0d1117;font-size:12px}}
</style></head>
<body>
<h2>ERGA-SS — TradingView vs Python Alignment Report</h2>
<p>Pine Script SS Line and flip signals compared to Python indicator on identical OHLCV bars.</p>
<table>
<thead><tr>
  <th>Ticker</th><th>TF</th><th>Bars</th>
  <th>TV Signals</th><th>Py Signals</th>
  <th>Max SS Diff</th><th>Avg SS Diff</th><th>Sig Match</th><th>Flip Detail</th>
</tr></thead>
<tbody>{rows_html}</tbody>
</table>
</body></html>"""

out_html = OUT_DIR / "alignment_report.html"
out_html.write_text(html)
print(f"\nSaved → {out_html}")
