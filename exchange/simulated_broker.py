"""
SimulatedBroker -- feeds engine.py historical option-chain snapshots for
backtest.py, and simulates fills with configurable slippage/fees.

IMPORTANT / UNRESOLVED (flagged per spec section 9, do not paper over):
Delta Exchange's public Historical Data API is confirmed to serve OHLCV
candles for a given product *symbol* at various resolutions, and the Tickers
API can return a live option chain for an underlying+expiry. What is NOT yet
confirmed is whether EXPIRED option contract symbols remain queryable for
historical candles after they expire -- most exchanges restrict or purge this.

This determines whether the backtest can be "fully realistic" (actual
historical option premiums) or must fall back to "reconstructed" (Black-
Scholes theoretical premiums computed from historical underlying price +
an assumed/estimated implied-volatility series, clearly labeled approximate).

`run_diagnostic.py` (separate script) checks this against the live API using
your credentials and reports which mode is available BEFORE you run a real
backtest. This class supports both:
  - HistoricalOptionDataSource: replays real recorded option premiums
  - ReconstructedOptionDataSource: Black-Scholes theoretical premiums

Swap via config.backtest_data_mode ('realistic' | 'reconstructed' | 'auto').
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional

from broker import Broker, OptionQuote, OrderResult, Position
from config import StrategyConfig


class OptionDataSource(ABC):
    """Pluggable source of historical option chain data for a point in time."""

    @abstractmethod
    def get_expiries(self, underlying: str, as_of_timestamp: int) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    def get_chain(self, underlying: str, expiry: str, as_of_timestamp: int) -> List[OptionQuote]:
        raise NotImplementedError


class HistoricalOptionDataSource(OptionDataSource):
    """
    Real recorded historical option premiums, pulled from Delta's API and
    cached locally (see data/fetch_historical_options.py, to be run once
    run_diagnostic.py confirms this data is available).

    Expects a local cache structured as:
        data/options_cache/{underlying}/{expiry}/{timestamp}.json
    """
    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir

    def get_expiries(self, underlying: str, as_of_timestamp: int) -> List[str]:
        raise NotImplementedError(
            "Populate from cached historical expiry listings once "
            "run_diagnostic.py confirms expired-option data is queryable."
        )

    def get_chain(self, underlying: str, expiry: str, as_of_timestamp: int) -> List[OptionQuote]:
        raise NotImplementedError(
            "Load cached historical option chain snapshot for this timestamp."
        )


class ReconstructedOptionDataSource(OptionDataSource):
    """
    Fallback: theoretical premiums via Black-Scholes, using historical
    underlying price (real, from Delta's candle API) and an assumed implied
    volatility. This is explicitly an APPROXIMATION -- it will not reflect
    real bid/ask spreads, skew, or liquidity at each historical moment.
    Every trade_entry/exit record produced this way must be tagged
    data_mode='reconstructed' in the trade log so results are never confused
    with a realistic backtest.
    """
    def __init__(self, underlying_price_series, assumed_iv: float = 0.55, risk_free_rate: float = 0.0):
        """
        underlying_price_series: callable(timestamp) -> float, backed by real
        historical candles (see exchange/delta_client.py get_historical_candles).
        assumed_iv: placeholder flat IV -- replace with a real vol surface if/when
        available; this is the single biggest source of error in reconstructed mode.
        """
        from config import CONFIG
        self.price_series = underlying_price_series
        self.assumed_iv = assumed_iv
        self.contract_value_underlying = CONFIG.contract_value_underlying
        self.risk_free_rate = risk_free_rate

    @staticmethod
    def _bs_price(spot, strike, t_years, iv, r, option_type) -> float:
        import math
        from statistics import NormalDist

        if spot <= 0 or strike <= 0:
            return 0.0
        if t_years <= 0:
            intrinsic = max(0.0, spot - strike) if option_type == "call" else max(0.0, strike - spot)
            return intrinsic

        d1 = (math.log(spot / strike) + (r + 0.5 * iv ** 2) * t_years) / (iv * math.sqrt(t_years))
        d2 = d1 - iv * math.sqrt(t_years)
        n = NormalDist()
        if option_type == "call":
            return spot * n.cdf(d1) - strike * math.exp(-r * t_years) * n.cdf(d2)
        else:
            return strike * math.exp(-r * t_years) * n.cdf(-d2) - spot * n.cdf(-d1)

    def get_expiries(self, underlying: str, as_of_timestamp: int) -> List[str]:
        import datetime
        today = datetime.datetime.utcfromtimestamp(as_of_timestamp).date()
        return [
            (today + datetime.timedelta(days=0)).isoformat(),
            (today + datetime.timedelta(days=1)).isoformat(),
        ]

    def get_chain(self, underlying: str, expiry: str, as_of_timestamp: int) -> List[OptionQuote]:
        import datetime
        spot = self.price_series(as_of_timestamp)
        expiry_dt = datetime.datetime.fromisoformat(expiry).replace(
            hour=17, minute=30, tzinfo=datetime.timezone.utc  # approximate Delta daily expiry time -- verify actual expiry time
        )
        now_dt = datetime.datetime.utcfromtimestamp(as_of_timestamp).replace(tzinfo=datetime.timezone.utc)
        t_years = max((expiry_dt - now_dt).total_seconds(), 0) / (365 * 24 * 3600)

        quotes = []
        # strikes at round increments around spot -- Delta's actual strike
        # spacing must be confirmed and substituted here (this is a placeholder
        # spacing of 1% of spot, common for BTC options but not verified against
        # Delta's live product list)
        step = max(round(spot * 0.01 / 50) * 50, 50)
        for i in range(-20, 21):
            strike = round((spot + i * step) / step) * step
            if strike <= 0:
                continue
            for otype in ("put", "call"):
                theo_per_underlying_unit = self._bs_price(spot, strike, t_years, self.assumed_iv, self.risk_free_rate, otype)
                # Premium per LOT = theoretical price per unit of underlying *
                # contract_value_underlying (UNVERIFIED, see config.py). Without
                # this scaling every premium comes out priced as if 1 lot = 1 BTC,
                # which is almost certainly wrong.
                premium_per_lot = theo_per_underlying_unit * self.contract_value_underlying
                quotes.append(OptionQuote(
                    symbol=f"{otype[0].upper()}-{underlying}-{int(strike)}-{expiry}",
                    option_type=otype,
                    strike=strike,
                    expiry=expiry,
                    premium=round(premium_per_lot, 2),
                    underlying_price=spot,
                    timestamp=as_of_timestamp,
                ))
        return quotes


class SimulatedBroker(Broker):
    def __init__(self, cfg: StrategyConfig, data_source: OptionDataSource):
        self.cfg = cfg
        self.data_source = data_source
        self._clock: int = 0
        self._open_positions: Dict[str, Position] = {}

    def set_clock(self, timestamp: int) -> None:
        """backtest.py calls this before each on_candle_close() so the broker
        knows 'now' for point-in-time data lookups."""
        self._clock = timestamp

    def get_available_expiries(self, underlying: str, as_of_timestamp: Optional[int] = None) -> List[str]:
        ts = as_of_timestamp if as_of_timestamp is not None else self._clock
        return self.data_source.get_expiries(underlying, ts)

    def get_option_chain(self, underlying: str, expiry: str, timestamp: Optional[int] = None) -> List[OptionQuote]:
        ts = timestamp if timestamp is not None else self._clock
        return self.data_source.get_chain(underlying, expiry, ts)

    def estimate_margin_per_lot(self, quote: OptionQuote) -> float:
        # PLACEHOLDER: Delta's real margin formula for short options must be
        # substituted here once confirmed (their margin methodology combines
        # premium received + an underlying-price-based risk add-on, similar to
        # SPAN). This flat approximation (15% of PER-LOT notional, scaled by
        # the unverified contract_value_underlying multiplier, floored at
        # premium) is NOT calibrated against real numbers -- do not trust
        # backtest P&L or live sizing until this is replaced with the verified
        # formula from Delta's margin-calculator endpoint.
        notional_per_lot = quote.strike * self.cfg.contract_value_underlying
        return max(quote.premium, notional_per_lot * 0.15)

    def place_sell_order(self, quote: OptionQuote, quantity: int) -> OrderResult:
        if quantity < 1:
            return OrderResult(False, None, None, quantity, "quantity_must_be_positive")
        slip = quote.premium * (self.cfg.slippage_pct / 100.0)
        fill_premium = max(0.0, quote.premium - slip)  # selling: slippage works against you (lower fill)
        pos_key = quote.symbol
        self._open_positions[pos_key] = Position(
            symbol=quote.symbol, option_type=quote.option_type, strike=quote.strike,
            expiry=quote.expiry, side="sell", quantity=quantity,
            entry_premium=fill_premium, entry_timestamp=self._clock,
            strategy_direction="BUY" if quote.option_type == "put" else "SELL",
        )
        return OrderResult(True, f"sim-{pos_key}-{self._clock}", fill_premium, quantity, "simulated_fill")

    def close_position(self, position: Position) -> OrderResult:
        # Look up current theoretical/historical premium for this exact contract at self._clock
        # Prefer the broker's configured underlying. Symbols may contain more
        # dashes than the simple C-BTC-STRIKE-DATE format.
        chain = self.get_option_chain(self.cfg.underlying_asset, position.expiry, self._clock)
        match = next((q for q in chain if q.strike == position.strike and q.option_type == position.option_type), None)
        if match is None:
            return OrderResult(False, None, None, position.quantity, "no_quote_available_at_close_time")
        slip = match.premium * (self.cfg.slippage_pct / 100.0)
        fill_premium = match.premium + slip  # buying back to close: slippage works against you (higher fill)
        self._open_positions.pop(position.symbol, None)
        return OrderResult(True, f"sim-close-{position.symbol}-{self._clock}", fill_premium, position.quantity, "simulated_close")

    def get_open_positions(self) -> List[Position]:
        return list(self._open_positions.values())
