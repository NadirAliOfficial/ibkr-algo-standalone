"""
Generate per-ticker alignment charts: TV Pine Script signals vs Python signals
on the same OHLCV candles.
"""
import os
import pandas as pd
import mplfinance as mpf
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from pathlib import Path

ALIGN_DIR = Path("docs/alignment")
OUT_DIR   = Path("docs/alignment/charts")
OUT_DIR.mkdir(parents=True, exist_ok=True)

for csv_path in sorted(ALIGN_DIR.glob("*_comparison.csv")):
    name   = csv_path.stem.replace("_comparison", "")
    parts  = name.split("_")
    ticker = parts[1]
    tf_min = parts[2]

    df = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    df.index = pd.to_datetime(df.index, utc=True)

    # Show last 120 bars (most recent / most relevant)
    df = df.iloc[-120:][["open","high","low","close","tv_buy","tv_sell","py_buy","py_sell","tv_ss","py_ss"]]

    has_tv = df["tv_ss"].notna().any()
    ss_ref = df["tv_ss"] if has_tv else df["py_ss"]

    py_buy  = ss_ref.where(df["py_buy"]  == 1)
    py_sell = ss_ref.where(df["py_sell"] == 1)

    apds = [mpf.make_addplot(ss_ref, color="#5b9cf6", width=1.5, panel=0)]

    if has_tv:
        tv_buy  = ss_ref.where(df["tv_buy"]  == 1)
        tv_sell = ss_ref.where(df["tv_sell"] == 1)
        if tv_buy.notna().any():
            apds.append(mpf.make_addplot(tv_buy,  type="scatter", markersize=180,
                                         marker="^", color="#00ff88", panel=0))
        if tv_sell.notna().any():
            apds.append(mpf.make_addplot(tv_sell, type="scatter", markersize=180,
                                         marker="v", color="#ff4466", panel=0))

    if py_buy.notna().any():
        apds.append(mpf.make_addplot(py_buy,  type="scatter", markersize=60,
                                     marker="^", color="#ffffff", panel=0))
    if py_sell.notna().any():
        apds.append(mpf.make_addplot(py_sell, type="scatter", markersize=60,
                                     marker="v", color="#ffcc00", panel=0))

    subtitle = "TV Pine Script vs Python ERGA-SS" if has_tv else "Python ERGA-SS signals"
    fig, axes = mpf.plot(
        df[["open","high","low","close"]],
        type="candle",
        style="charles",
        title=f"{ticker} {tf_min}m — {subtitle} (last 120 bars)\n"
              f"{'Large ▲▼ = TradingView   Small ▲▼ = Python' if has_tv else '▲ = BUY   ▼ = SELL'}",
        addplot=apds,
        figsize=(18, 8),
        returnfig=True,
    )

    legend_handles = [mpatches.Patch(color="#5b9cf6", label="SS Line")]
    if has_tv:
        legend_handles += [
            mpatches.Patch(color="#00ff88", label="TV BUY  (Pine Script)"),
            mpatches.Patch(color="#ff4466", label="TV SELL (Pine Script)"),
        ]
    legend_handles += [
        mpatches.Patch(color="#ffffff", label="Py BUY  (Python)"),
        mpatches.Patch(color="#ffcc00", label="Py SELL (Python)"),
    ]
    axes[0].legend(handles=legend_handles, loc="upper left", fontsize=9,
                   facecolor="#161b22", edgecolor="#30363d", labelcolor="white")

    out = OUT_DIR / f"{name}.png"
    fig.savefig(out, dpi=120, bbox_inches="tight", facecolor="#0d1117")
    plt.close(fig)
    print(f"  Saved {out.name}")

print(f"\nAll charts → {OUT_DIR}")
