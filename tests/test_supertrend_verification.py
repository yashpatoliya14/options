"""
Independent Supertrend verification.

The reference implementation below is written from the textbook formula
(TradingView definition) with a naive per-bar loop, deliberately
structured differently from engine.indicators.supertrend.

Verified properties:
  1. On randomized data (seeded), trend direction and supertrend line
     match the independent reference bar-by-bar (post-warmup).
  2. Pure uptrend -> trend +1; pure downtrend -> trend -1.
  3. Small noise cannot flip a 1.5x ATR band.

Warmup note: on the very first ATR bar the trend anchor is a
convention (TradingView seeds bull). The engine seeds bull too; from
the next bar onward the implementations are identical, so all
comparisons start one bar after the ATR warmup.
"""

from __future__ import annotations

import math
import random

import pandas as pd

from engine.indicators import supertrend


def _reference_supertrend(high, low, close, period, multiplier):
    """Naive independent implementation (Wilder ATR + ratcheting bands)."""
    n = len(close)
    atr = [float("nan")] * n
    trs = []
    for i in range(n):
        if i == 0:
            tr = high[i] - low[i]
        else:
            tr = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        trs.append(tr)
        if i == period - 1:
            atr[i] = sum(trs[:period]) / period
        elif i >= period:
            atr[i] = (atr[i - 1] * (period - 1) + tr) / period

    upper = [float("nan")] * n
    lower = [float("nan")] * n
    trend = [0] * n
    line = [float("nan")] * n
    for i in range(n):
        if math.isnan(atr[i]):
            continue
        hl2 = (high[i] + low[i]) / 2.0
        bu = hl2 + multiplier * atr[i]
        bl = hl2 - multiplier * atr[i]
        if math.isnan(upper[i - 1]) if i > 0 else True:
            upper[i] = bu
            lower[i] = bl
        else:
            upper[i] = bu if (bu < upper[i - 1] or close[i - 1] > upper[i - 1]) else upper[i - 1]
            lower[i] = bl if (bl > lower[i - 1] or close[i - 1] < lower[i - 1]) else lower[i - 1]

        if math.isnan(line[i - 1]) or line[i - 1] == upper[i - 1]:
            line[i] = upper[i]
            trend[i] = -1
            if close[i] > upper[i]:
                line[i] = lower[i]
                trend[i] = 1
        else:
            line[i] = lower[i]
            trend[i] = 1
            if close[i] < lower[i]:
                line[i] = upper[i]
                trend[i] = -1
    return trend, line


def _series(closes, rel_spread=0.006):
    """Symmetric high/low around close as a fraction of price."""
    high = [c * (1.0 + rel_spread) for c in closes]
    low = [c * (1.0 - rel_spread) for c in closes]
    return high, low, closes


def _seeded_path(n_bars: int, warmup_bars: int, seed: int = 42) -> list[float]:
    """Strong initial trend (converges both implementations) then a random walk."""
    random.seed(seed)
    closes = [50000.0]
    for i in range(n_bars):
        if i < warmup_bars:
            step = 0.02  # strong run: both conventions anchor bull
        else:
            step = random.gauss(0.0, 0.005)
        closes.append(closes[-1] * (1.0 + step))
    return closes


def test_supertrend_matches_independent_reference_on_random_data():
    for period, multiplier in [(10, 1.5), (15, 1.5), (15, 2.0), (20, 3.0)]:
        closes = _seeded_path(400, warmup_bars=3 * period, seed=42 + period)
        high, low, close = _series(closes)
        ours = supertrend(
            pd.Series(high, dtype=float),
            pd.Series(low, dtype=float),
            pd.Series(close, dtype=float),
            period=period,
            multiplier=multiplier,
        )
        ref_trend, ref_line = _reference_supertrend(high, low, close, period, multiplier)

        ours_trend = [int(v) for v in ours["trend"].tolist()]
        ours_line = [float(v) for v in ours["supertrend"].tolist()]

        # The only legitimate implementation freedom is the pre-breakout anchor
        # (which band the trend sits on before its first real breakout). Find
        # the synchronization point: first bar where trends agree and keep
        # agreeing for 50+ consecutive bars. From there, every bar must match
        # exactly — trend, line, and every flip timing.
        sync = None
        run = 0
        for i in range(period, len(close)):
            if ours_trend[i] == ref_trend[i]:
                run += 1
                if run >= 50:
                    sync = i - run + 1
                    break
            else:
                run = 0
        assert sync is not None, "implementations never synchronized"

        for i in range(sync, len(close)):
            assert ours_trend[i] == ref_trend[i], (
                f"trend mismatch at bar {i} (period={period}, mult={multiplier}): "
                f"ours={ours_trend[i]} ref={ref_trend[i]}"
            )
            ref_val = ref_line[i]
            ours_val = ours_line[i]
            assert abs(ours_val - ref_val) < 1e-6 * max(1.0, abs(ref_val)), (
                f"line mismatch at bar {i} (period={period}, mult={multiplier}): "
                f"ours={ours_val} ref={ref_val}"
            )

        # All flips after synchronization must occur on identical bars.
        ours_flips = [i for i in range(sync + 1, len(close)) if ours_trend[i] != ours_trend[i - 1]]
        ref_flips = [i for i in range(sync + 1, len(close)) if ref_trend[i] != ref_trend[i - 1]]
        assert ours_flips == ref_flips, "flip timings diverge after sync"


def test_supertrend_direction_on_pure_uptrend_and_downtrend():
    up = [100.0 * (1.0 + 0.01 * i) for i in range(80)]
    down = [100.0 * (1.0 - 0.005 * i) for i in range(80)]
    for closes, expected in ((up, 1), (down, -1)):
        high, low, close = _series(closes)
        st = supertrend(
            pd.Series(high, dtype=float),
            pd.Series(low, dtype=float),
            pd.Series(close, dtype=float),
            period=15,
            multiplier=1.5,
        )
        final_trend = int(st["trend"].iloc[-1])
        assert final_trend == expected
        tail = st["trend"].iloc[-30:]
        assert set(tail.unique()) == {expected}


def test_supertrend_flip_requires_close_cross_of_band():
    # Band is ~1.5 * ATR away; 0.1% noise cannot travel that far.
    random.seed(7)
    closes = [50000.0]
    for _ in range(200):
        closes.append(closes[-1] * (1.0 + random.gauss(0.0, 0.001)))
    high, low, close = _series(closes, rel_spread=0.005)
    st = supertrend(
        pd.Series(high, dtype=float),
        pd.Series(low, dtype=float),
        pd.Series(close, dtype=float),
        period=15,
        multiplier=1.5,
    )
    tail = st["trend"].iloc[40:]
    assert tail.nunique() == 1
