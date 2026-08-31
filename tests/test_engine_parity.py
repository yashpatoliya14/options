from datetime import datetime, timezone

import pandas as pd

from engine import Signal, StrategyEngine, StrategyParams


def test_same_engine_same_inputs_same_outputs():
    engine = StrategyEngine(StrategyParams(ema_fast=2, ema_slow=4, credit_min=150, credit_max=200, spread_width=100))
    candles = pd.DataFrame(
        {
            "timestamp": pd.date_range(datetime(2026, 1, 1, tzinfo=timezone.utc), periods=5, freq="min"),
            "open": [10, 9, 8, 7, 12],
            "high": [11, 10, 9, 8, 13],
            "low": [9, 8, 7, 6, 11],
            "close": [10, 9, 8, 7, 12],
            "volume": [1, 1, 1, 1, 1],
        }
    )
    signal = Signal(candles["timestamp"].iloc[-1].to_pydatetime(), "bull", 1.0, 0.5)
    chain = {
        "2026-01-01": pd.DataFrame(
            [
                {"symbol": "P900", "option_type": "put", "strike": 900, "mark": 250, "underlying_price": 1000},
                {"symbol": "P800", "option_type": "put", "strike": 800, "mark": 70, "underlying_price": 1000},
            ]
        )
    }

    first = (
        engine.detect_crossover(candles),
        engine.select_expiry_and_spread(signal, chain),
    )
    second = (
        engine.detect_crossover(candles),
        engine.select_expiry_and_spread(signal, chain),
    )

    assert first == second


def test_engine_package_has_no_io_or_exchange_imports():
    import ast
    from pathlib import Path

    banned_roots = {"requests", "websocket", "time", "exchange", "os", "pathlib", "sqlite3"}
    banned_calls = {"open"}
    engine_root = Path(__file__).resolve().parents[1] / "engine"
    for path in engine_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in banned_roots, f"{path} imports {alias.name}"
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in banned_roots, f"{path} imports {node.module}"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in banned_calls, f"{path} calls {node.func.id}"
