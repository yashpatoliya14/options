"""Entry point: run the BTCUSD SuperTrend-directional premium-harvest backtest.

Usage:
    python run_backtest.py            # full run, writes results/ CSVs + markdown
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from backtest import run_backtest       # noqa: E402
from report import write_reports        # noqa: E402


def main() -> None:
    trades, ledger, _ = run_backtest(verbose=True)
    df, summary = write_reports(trades, ledger)
    print()
    print(summary)
    print(f"\nWrote {len(df)} trades to results/trades.csv")
    print("Wrote results/backtest_results.md")


if __name__ == "__main__":
    main()
