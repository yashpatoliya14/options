from __future__ import annotations

from datetime import datetime

import pandas as pd

from .black_scholes import greeks as bs_greeks
from .config_schema import StrategyParams
from .models import Leg, SpreadCandidate


def mid_price(row: pd.Series) -> float:
    bid = _num(row, "bid")
    ask = _num(row, "ask")
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    for column in ("mark", "mid", "premium"):
        value = _num(row, column)
        if value is not None:
            return value
    raise ValueError("option row has no executable price")


def bid_ask_pct(row: pd.Series) -> float | None:
    bid = _num(row, "bid")
    ask = _num(row, "ask")
    if bid is None or ask is None or bid <= 0:
        return None
    return (ask - bid) / bid


def executable_price(row: pd.Series, side: str, fill_model: str, slippage_pct: float) -> float:
    """Buy at ask, sell at bid unless fill_model is mark."""
    bid = _num(row, "bid")
    ask = _num(row, "ask")
    mark = mid_price(row)
    if fill_model == "mark" or bid is None or ask is None:
        raw = mark
        if side == "buy":
            return raw * (1.0 + slippage_pct)
        return raw * (1.0 - slippage_pct)
    if side == "buy":
        return ask * (1.0 + slippage_pct)
    return bid * (1.0 - slippage_pct)


def passes_liquidity(row: pd.Series, params: StrategyParams) -> bool:
    if not params.liquidity_filter_enabled:
        return True
    spread = bid_ask_pct(row)
    if spread is not None and spread > params.max_bid_ask_pct:
        return False
    volume = _num(row, "volume")
    if volume is not None and volume < params.min_volume:
        return False
    oi = _num(row, "open_interest")
    if oi is not None and oi < params.min_open_interest:
        return False
    return True


def dte_days(expiry: str, now: datetime) -> float:
    """DTE in fractional days to the expiry's ACTUAL settlement time.

    Delta India options settle at 12:30 UTC (18:00 IST); measuring to
    expiry midnight overstates remaining life by 11.5 hours and skews
    DTE-based expiry scoring and min/max DTE filters."""
    expiry_ts = pd.Timestamp(expiry)
    if expiry_ts.tzinfo is None:
        expiry_ts = expiry_ts.tz_localize("UTC")
    else:
        expiry_ts = expiry_ts.tz_convert("UTC")
    expiry_ts = expiry_ts + pd.Timedelta(hours=12, minutes=30)
    now_ts = pd.Timestamp(now)
    if now_ts.tzinfo is None:
        now_ts = now_ts.tz_localize("UTC")
    return (expiry_ts - now_ts).total_seconds() / 86400.0


def select_expiry_date(
    expiries: list[str],
    now: datetime,
    params: StrategyParams,
    cutoff_choice: str,
) -> list[str]:
    """Return preferred expiry keys in evaluation order."""
    if not expiries:
        return []
    if params.expiry_selection == "cutoff_hour":
        if cutoff_choice == "same_day":
            return expiries[:1]
        return expiries[1:2] if len(expiries) > 1 else expiries[:1]

    scored: list[tuple[float, str]] = []
    for expiry in expiries:
        days = dte_days(expiry, now)
        if days < params.min_dte - 1e-6 or days > params.max_dte + 1e-6:
            continue
        scored.append((abs(days - params.target_dte), expiry))
    scored.sort()
    ordered = [item[1] for item in scored]
    return ordered[:2] if ordered else expiries[:1]


def target_short_put_strike(
    spot: float,
    chain: pd.DataFrame,
    params: StrategyParams,
    atr: float | None,
    iv: float | None,
    t_years: float,
) -> float:
    mode = params.strike_selection
    if mode == "delta":
        deltas = chain.get("delta")
        if deltas is not None and deltas.notna().any():
            puts = chain.copy()
            puts["abs_delta"] = puts["delta"].abs()
            puts["dist"] = (puts["abs_delta"] - params.target_short_delta).abs()
            return float(puts.sort_values("dist").iloc[0]["strike"])
    if mode == "vol_adjusted":
        vol = iv if iv and iv > 0 else 0.0
        atr_term = (atr / spot) if atr and spot > 0 else 0.0
        iv_term = vol * (max(t_years, 1.0 / 365.0) ** 0.5)
        distance = params.vol_adjusted_k * max(atr_term, iv_term, params.strike_offset_pct)
        return spot * (1.0 - distance)
    if mode == "atm":
        return spot
    if mode == "atm_or_nearest_otm":
        return spot
    return spot * (1.0 - params.strike_offset_pct)


def target_long_put_strike(spot: float, chain: pd.DataFrame, params: StrategyParams) -> float:
    """Bear Put Spread long-put target: |delta| ≈ target_long_delta (ITM put)."""
    if "delta" in chain.columns and chain["delta"].notna().any():
        puts = chain.copy()
        puts["abs_delta"] = puts["delta"].abs()
        puts["dist"] = (puts["abs_delta"] - params.target_long_delta).abs()
        return float(puts.sort_values("dist").iloc[0]["strike"])
    return spot * (1.0 + params.strike_offset_pct)


def score_spread(
    candidate: SpreadCandidate,
    short_row: pd.Series,
    long_row: pd.Series,
    params: StrategyParams,
    direction_ok: float = 1.0,
) -> float:
    if not params.spread_scoring_enabled:
        return 0.0
    liq = 0.0
    short_spread = bid_ask_pct(short_row) or 0.0
    long_spread = bid_ask_pct(long_row) or 0.0
    liq -= params.score_spread_penalty * (short_spread + long_spread)
    delta_score = 0.0
    short_delta = _num(short_row, "delta")
    if short_delta is not None:
        delta_score = -abs(abs(short_delta) - params.target_short_delta)
    gamma_pen = params.score_gamma_penalty * (
        abs(_num(short_row, "gamma") or 0.0) + abs(_num(long_row, "gamma") or 0.0)
    )
    iv_score = 0.0
    iv = _num(short_row, "iv")
    iv_ref = getattr(params, "_iv_ref", None)
    if iv is not None and iv_ref:
        iv_score = (iv - iv_ref) / max(iv_ref, 1e-6)
    dte_days_val = dte_days(candidate.expiry, short_row.get("_now", pd.Timestamp.now().to_pydatetime()))
    dte_score = -abs(dte_days_val - params.target_dte) / max(params.target_dte, 1e-6)
    return (
        params.score_direction_weight * direction_ok
        + params.score_liquidity_weight * liq
        + params.score_delta_weight * delta_score
        + params.score_iv_weight * iv_score
        + params.score_dte_weight * dte_score
        - gamma_pen
    )


def net_spread_credit(short_row: pd.Series, long_row: pd.Series, params: StrategyParams) -> float:
    short_fill = executable_price(short_row, "sell", params.fill_model, params.slippage_pct)
    long_fill = executable_price(long_row, "buy", params.fill_model, params.slippage_pct)
    return short_fill - long_fill


def enrich_chain_greeks(
    chain: pd.DataFrame,
    now: datetime,
    expiry: str,
    rate: float,
) -> pd.DataFrame:
    """Fill missing greeks from Black-Scholes when IV and spot exist."""
    if chain.empty:
        return chain
    out = chain.copy()
    t_years = max(dte_days(expiry, now), 0.0) / 365.0
    for idx, row in out.iterrows():
        if _num(row, "delta") is not None:
            continue
        iv = _num(row, "iv")
        spot = _num(row, "underlying_price") or _num(row, "spot")
        strike = _num(row, "strike")
        option_type = str(row.get("option_type", "put")).lower()
        if iv is None or spot is None or strike is None:
            continue
        g = bs_greeks(spot, strike, t_years, iv, rate, option_type)
        out.at[idx, "delta"] = g["delta"]
        out.at[idx, "gamma"] = g["gamma"]
        out.at[idx, "theta"] = g["theta"]
        out.at[idx, "vega"] = g["vega"]
    return out


def expected_slippage_pct(short_row: pd.Series, long_row: pd.Series) -> float:
    values = [v for v in (bid_ask_pct(short_row), bid_ask_pct(long_row)) if v is not None]
    if not values:
        return 0.0
    return sum(values) / len(values)


def qty_for_risk(params: StrategyParams, max_loss_per_contract: float, equity: float | None = None) -> int:
    """
    Notional-correct fixed-risk sizing.

    max_loss_per_contract must be in USD notional: (width - credit) * contract_size
    for credit spreads, debit * contract_size for debit spreads.
    """
    if params.sizing_mode != "fixed_risk" or max_loss_per_contract <= 0:
        return max(int(params.qty), 1)
    capital = equity if equity is not None else params.capital
    risk_budget = capital * params.risk_pct
    qty = int(risk_budget // max_loss_per_contract)
    max_qty = int(capital * params.max_risk_pct // max_loss_per_contract)
    qty = min(qty, max_qty)
    return max(qty, 0)


def drawdown_risk_multiplier(params: StrategyParams, drawdown_frac: float) -> float:
    """Risk multiplier in [dd_risk_floor_pct, 1.0]; 1.0 when not in drawdown."""
    if not params.dd_risk_reduction or drawdown_frac <= 0:
        return 1.0
    threshold = max(params.dd_risk_threshold, 1e-6)
    scale = max(1.0 - drawdown_frac / threshold, params.dd_risk_floor_pct)
    return scale


def _num(row: pd.Series, column: str) -> float | None:
    if column not in row.index:
        return None
    value = row[column]
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clone_leg_qty(leg: Leg, qty: int) -> Leg:
    return Leg(
        symbol=leg.symbol,
        option_type=leg.option_type,
        strike=leg.strike,
        expiry=leg.expiry,
        side=leg.side,
        qty=qty,
        product_id=leg.product_id,
    )
