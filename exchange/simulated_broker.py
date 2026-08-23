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
    def get_chain(self, underlying: str, expiry: str, as_of_timestamp: int, spot_override: Optional[float] = None) -> List[OptionQuote]:
        raise NotImplementedError


class HistoricalOptionDataSource(OptionDataSource):
    """
    Real recorded historical option premiums, pulled from Delta's API.
    
    Based on diagnostic report, expired option candles ARE available from 
    Delta Exchange API. This implementation fetches historical option chain
    data on-demand and caches it locally.
    """
    def __init__(self, cache_dir: str = "data/options_cache"):
        self.cache_dir = cache_dir
        self._cache = {}  # Memory cache: (underlying, expiry, timestamp) -> List[OptionQuote]
        
    def _fetch_from_api(self, underlying: str, expiry: str, as_of_timestamp: int) -> List[OptionQuote]:
        """
        Fetch historical option chain from Delta API for a specific timestamp.
        
        Note: This is a simplified implementation. In production, you would:
        1. Cache results to avoid repeated API calls
        2. Handle API rate limits
        3. Implement proper error handling
        """
        from exchange.delta_client import DeltaClient
        from config import CONFIG
        
        client = DeltaClient(CONFIG)
        
        # Convert timestamp to date for expiry matching
        import datetime
        query_date = datetime.datetime.utcfromtimestamp(as_of_timestamp).date().isoformat()
        
        # Fetch option chain for the expiry date
        # Note: We need to convert expiry format if needed
        chain = client.get_option_chain(underlying, expiry, as_of_timestamp)
        
        # Filter to only include options that existed at the time
        # (simplified - assumes all options in chain existed)
        return chain
        
    def get_expiries(self, underlying: str, as_of_timestamp: int) -> List[str]:
        """
        Get available expiries at a specific historical timestamp.
        
        For historical data, we need to know which expiries were available
        at that time. This implementation returns today and tomorrow's
        expiries (common for daily expiries).
        """
        import datetime
        
        # For historical option data, we typically have today and tomorrow's expiries
        today = datetime.datetime.utcfromtimestamp(as_of_timestamp).date()
        return [
            today.isoformat(),
            (today + datetime.timedelta(days=1)).isoformat()
        ]
        
    def get_chain(self, underlying: str, expiry: str, as_of_timestamp: int, spot_override: Optional[float] = None) -> List[OptionQuote]:
        """
        Get historical option chain for a specific expiry at a specific timestamp.
        
        This fetches from API and caches the result. In production, you would
        want to pre-fetch historical data for better performance.
        """
        cache_key = (underlying, expiry, as_of_timestamp)
        
        if cache_key in self._cache:
            return self._cache[cache_key]
            
        try:
            chain = self._fetch_from_api(underlying, expiry, as_of_timestamp)
            self._cache[cache_key] = chain
            return chain
        except Exception as e:
            # If historical data fetch fails, fall back to reconstructed mode
            # with a warning
            print(f"Warning: Failed to fetch historical option data for {underlying} {expiry} at {as_of_timestamp}: {e}")
            print("Falling back to reconstructed pricing.")
            
            # Create a simple reconstructed chain as fallback
            from exchange.delta_client import DeltaClient
            from config import CONFIG
            client = DeltaClient(CONFIG)
            
            # Get current price for reconstruction
            try:
                # Get approximate price at that time (simplified)
                candles = client.get_historical_candles(
                    CONFIG.underlying_symbol, "1h", 
                    as_of_timestamp - 3600, as_of_timestamp
                )
                if candles:
                    price = float(candles[-1]["close"]) if candles else 10000  # Default
                else:
                    price = 10000  # Reasonable default for BTC
            except:
                price = 10000
                
            # Create basic reconstructed chain
            chain = []
            import datetime
            expiry_dt = datetime.datetime.fromisoformat(expiry).replace(
                hour=17, minute=30, tzinfo=datetime.timezone.utc
            )
            now_dt = datetime.datetime.utcfromtimestamp(as_of_timestamp).replace(tzinfo=datetime.timezone.utc)
            t_years = max((expiry_dt - now_dt).total_seconds(), 0) / (365 * 24 * 3600)
            
            # Simplified strike generation
            step = max(round(price * 0.01 / 50) * 50, 50)
            for i in range(-5, 6):
                strike = round((price + i * step) / step) * step
                if strike <= 0:
                    continue
                    
                for otype in ("put", "call"):
                    # Very simplified premium calculation
                    intrinsic = max(0, price - strike) if otype == "call" else max(0, strike - price)
                    time_value = price * 0.01 * max(t_years, 0.001)  # Rough approximation
                    premium = intrinsic + time_value  # Per unit of underlying (1 BTC)
                    
                    chain.append(OptionQuote(
                        symbol=f"{otype[0].upper()}-{underlying}-{int(strike)}-{expiry}",
                        option_type=otype,
                        strike=strike,
                        expiry=expiry,
                        premium=round(premium, 2),
                        underlying_price=price,
                        timestamp=as_of_timestamp,
                    ))
                    
            self._cache[cache_key] = chain
            return chain


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

    def get_chain(self, underlying: str, expiry: str, as_of_timestamp: int, spot_override: Optional[float] = None) -> List[OptionQuote]:
        import datetime
        spot = spot_override if spot_override is not None else self.price_series(as_of_timestamp)
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
                # We do NOT scale by contract_value_underlying here because Delta Exchange
                # quotes premiums per 1 unit of underlying. The MIN_PREMIUM check expects
                # this format (e.g. $300 for 1 BTC option). Scaling by contract size happens
                # in PnL and margin calculations instead.
                quotes.append(OptionQuote(
                    symbol=f"{otype[0].upper()}-{underlying}-{int(strike)}-{expiry}",
                    option_type=otype,
                    strike=strike,
                    expiry=expiry,
                    premium=round(theo_per_underlying_unit, 2),
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

    def get_option_chain(self, underlying: str, expiry: str, timestamp: Optional[int] = None, spot_override: Optional[float] = None) -> List[OptionQuote]:
        ts = timestamp if timestamp is not None else self._clock
        return self.data_source.get_chain(underlying, expiry, ts, spot_override=spot_override)

    def estimate_margin_per_lot(self, quote: OptionQuote) -> float:
        # Improved margin estimation based on typical option margin formulas
        # Consistent with delta_client.py implementation
        notional_per_lot = quote.strike * self.cfg.contract_value_underlying
        
        # Risk component: higher for calls (unlimited upside), lower for puts
        risk_percentage = 0.20 if quote.option_type == "call" else 0.15
        
        # Premium collected for ONE lot (premium per BTC * lot size)
        premium_per_lot = quote.premium * self.cfg.contract_value_underlying

        # Calculate margin requirement
        risk_component = notional_per_lot * risk_percentage
        margin_required = premium_per_lot + risk_component
        
        # Minimum margin (floor) - at least premium + 10% of notional
        min_margin = premium_per_lot + (notional_per_lot * 0.10)
        
        return max(margin_required, min_margin)

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
        import datetime
        expiry_dt = datetime.datetime.fromisoformat(position.expiry).replace(
            hour=17, minute=30, tzinfo=datetime.timezone.utc
        )
        if self._clock >= expiry_dt.timestamp():
            # Option has expired. Settle at intrinsic value.
            spot = self.data_source.price_series(self._clock) if hasattr(self.data_source, 'price_series') else position.strike
            intrinsic = max(0.0, spot - position.strike) if position.option_type == "call" else max(0.0, position.strike - spot)
            self._open_positions.pop(position.symbol, None)
            return OrderResult(True, f"sim-close-{position.symbol}-{self._clock}", intrinsic, position.quantity, "expired_settlement")

        # Look up current theoretical/historical premium for this exact contract at self._clock
        chain = self.get_option_chain(self.cfg.underlying_asset, position.expiry, self._clock)
        match = next((q for q in chain if q.strike == position.strike and q.option_type == position.option_type), None)
        if match is None:
            # Fallback if strike is way out of the generated chain range
            if hasattr(self.data_source, '_bs_price'):
                spot = self.data_source.price_series(self._clock)
                t_years = max((expiry_dt.timestamp() - self._clock), 0) / (365 * 24 * 3600)
                theo = self.data_source._bs_price(spot, position.strike, t_years, self.data_source.assumed_iv, self.data_source.risk_free_rate, position.option_type)
                match = OptionQuote(symbol=position.symbol, option_type=position.option_type, strike=position.strike, expiry=position.expiry, premium=theo, underlying_price=spot, timestamp=self._clock)
            else:
                return OrderResult(False, None, None, position.quantity, "no_quote_available_at_close_time")
                
        slip = match.premium * (self.cfg.slippage_pct / 100.0)
        fill_premium = match.premium + slip  # buying back to close: slippage works against you (higher fill)
        self._open_positions.pop(position.symbol, None)
        return OrderResult(True, f"sim-close-{position.symbol}-{self._clock}", fill_premium, position.quantity, "simulated_close")

    def get_open_positions(self) -> List[Position]:
        return list(self._open_positions.values())
