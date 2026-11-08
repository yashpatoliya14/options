/**
 * Delta Exchange India API Type Definitions
 */

export type DeltaContractType = 'perpetual_futures' | 'futures' | 'call_options' | 'put_options' | 'interest_rate_swaps' | 'spot';

export interface DeltaProduct {
  id: number;
  symbol: string;
  description: string;
  contract_type: DeltaContractType;
  contract_value: string; // e.g. "0.001"
  contract_unit_currency: string; // e.g. "XAUT" or "PAXG"
  tick_size: string; // e.g. "0.01"
  trading_status: string; // e.g. "operational"
  state: string; // e.g. "live"
  is_quanto: boolean;
  notional_type: string;
  settling_asset: {
    id: number;
    symbol: string;
    precision: number;
  };
  quoting_asset: {
    id: number;
    symbol: string;
    precision: number;
  };
  underlying_asset: {
    id: number;
    symbol: string;
    precision: number;
  };
  maker_commission_rate: string;
  taker_commission_rate: string;
  initial_margin: string;
  maintenance_margin: string;
  default_leverage: string;
  max_leverage_notional: string;
  position_size_limit?: number;
  impact_size?: number;
}

export interface DeltaTicker {
  product_id: number;
  symbol: string;
  contract_type: string;
  mark_price: string;
  spot_price: string;
  close: number;
  open: number;
  high: number;
  low: number;
  volume: number;
  turnover: number;
  funding_rate?: string;
  timestamp: number;
  quotes?: {
    best_bid?: string;
    best_ask?: string;
    bid_size?: string;
    ask_size?: string;
  };
}

export interface DeltaCandle {
  time: number; // Unix timestamp in seconds
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface DeltaOrderRequest {
  product_id: number;
  size: number; // In contracts
  side: 'buy' | 'sell';
  order_type: 'limit_order' | 'market_order';
  limit_price?: string;
  time_in_force?: 'gtc' | 'ioc' | 'fok';
  post_only?: boolean;
  reduce_only?: boolean;
  client_order_id?: string;
}

export interface DeltaOrderResponse {
  id: number;
  product_id: number;
  side: 'buy' | 'sell';
  order_type: 'limit_order' | 'market_order';
  size: number;
  unfilled_size: number;
  limit_price: string;
  average_fill_price?: string;
  state: 'open' | 'pending' | 'closed' | 'cancelled' | 'rejected';
  client_order_id?: string;
  created_at: string;
  updated_at: string;
  cancellation_reason?: string;
}

export interface DeltaPosition {
  product_id: number;
  product_symbol: string;
  size: number; // positive = long, negative = short
  entry_price: string;
  margin: string;
  liquidation_price?: string;
  bankruptcy_price?: string;
  realized_pnl: string;
  unrealized_pnl: string;
  commission: string;
  leverage: string;
}

export interface DeltaWalletBalance {
  asset_id: number;
  asset_symbol: string;
  balance: string;
  available_balance: string;
  order_margin: string;
  position_margin: string;
}

