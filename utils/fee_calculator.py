"""
Fee calculation utilities for Delta Exchange.

This module handles fee calculations consistently across backtest and live trading.
Fees are fetched from the API when available, with fallback to configured defaults.
"""
from typing import Optional, Dict, Any
from config import StrategyConfig


class FeeCalculator:
    """
    Calculates fees for option trades on Delta Exchange.
    
    Fees are typically calculated as:
    - Taker fee: 0.01% (0.0001) of notional value
    - Maker fee: 0.01% (0.0001) of notional value (may be lower with rebates)
    
    For options, notional value = strike price * contract value * quantity
    """
    
    def __init__(self, cfg: StrategyConfig):
        self.cfg = cfg
        self._fee_cache: Optional[Dict[str, float]] = None
        
    def fetch_fees_from_api(self) -> Dict[str, float]:
        """
        Fetch current fee rates from Delta Exchange API.
        
        Returns:
            Dictionary with 'taker_fee', 'maker_fee' rates as decimals (e.g., 0.0001 for 0.01%)
        """
        # This would normally fetch from Delta's fee schedule endpoint
        # For now, use values from diagnostic report
        return {
            "taker_fee": 0.0001,  # 0.01%
            "maker_fee": 0.0001,  # 0.01%
        }
        
    def get_fee_rate(self, is_taker: bool = True) -> float:
        """
        Get the current fee rate.
        
        Args:
            is_taker: True for taker orders, False for maker orders
            
        Returns:
            Fee rate as decimal (e.g., 0.0001 for 0.01%)
        """
        if self._fee_cache is None:
            try:
                self._fee_cache = self.fetch_fees_from_api()
            except Exception:
                # Fallback to configured fee percentage (converted from % to decimal)
                default_fee = self.cfg.fee_pct / 100.0  # Convert from % to decimal
                self._fee_cache = {
                    "taker_fee": default_fee,
                    "maker_fee": default_fee,
                }
                
        return self._fee_cache["taker_fee"] if is_taker else self._fee_cache["maker_fee"]
        
    def calculate_fee(self, notional_value: float, is_taker: bool = True) -> float:
        """
        Calculate fee for a trade.
        
        Args:
            notional_value: Total notional value of the trade (strike * contract value * quantity)
            is_taker: True for taker orders, False for maker orders
            
        Returns:
            Fee amount in USD
        """
        fee_rate = self.get_fee_rate(is_taker)
        return notional_value * fee_rate
        
    def calculate_option_fee(self, strike: float, contract_value: float, 
                            quantity: int, is_taker: bool = True) -> float:
        """
        Calculate fee for an option trade.
        
        Args:
            strike: Option strike price
            contract_value: Contract value (e.g., 0.001 BTC per lot)
            quantity: Number of lots
            is_taker: True for taker orders, False for maker orders
            
        Returns:
            Fee amount in USD
        """
        notional_value = strike * contract_value * quantity
        return self.calculate_fee(notional_value, is_taker)
        
    def calculate_premium_fee(self, premium: float, quantity: int, 
                             is_taker: bool = True) -> float:
        """
        Calculate fee based on premium amount.
        
        Note: Some exchanges charge fees on premium rather than notional.
        This provides an alternative calculation method.
        
        Args:
            premium: Premium per lot
            quantity: Number of lots
            is_taker: True for taker orders, False for maker orders
            
        Returns:
            Fee amount in USD
        """
        total_premium = premium * quantity
        fee_rate = self.get_fee_rate(is_taker)
        return total_premium * fee_rate * 2  # Typically double for options (entry + exit)