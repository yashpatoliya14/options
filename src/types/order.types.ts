/**
 * Internal Order & Execution Types
 */

export type OrderPurpose = 'PRIMARY_INITIAL' | 'GRID_ENTRY' | 'HEDGE_ENTRY' | 'HEDGE_TP' | 'REVERSAL_CLOSE' | 'EMERGENCY_CLOSE';
export type InternalOrderStatus = 'PENDING' | 'SUBMITTED' | 'OPEN' | 'PARTIALLY_FILLED' | 'FILLED' | 'CANCELLED' | 'REJECTED';

export interface InternalOrder {
  id: string;
  strategyId: string;
  exchangeOrderId?: string;
  clientOrderId: string;
  productId: number;
  symbol: string;
  side: 'buy' | 'sell';
  orderType: 'limit_order' | 'market_order';
  price?: number;
  quantityContracts: number;
  filledContracts: number;
  averageFillPrice?: number;
  status: InternalOrderStatus;
  purpose: OrderPurpose;
  createdAt: number;
  updatedAt: number;
  error?: string;
}

export interface InternalPosition {
  strategyId: string;
  productId: number;
  symbol: string;
  side: 'LONG' | 'SHORT';
  quantityContracts: number;
  averageEntryPrice: number;
  currentMarkPrice: number;
  unrealizedPnlUsd: number;
  realizedPnlUsd: number;
  leverage: number;
  updatedAt: number;
}

export interface TradeRecord {
  id: string;
  strategyId: string;
  orderId: string;
  productId: number;
  symbol: string;
  side: 'buy' | 'sell';
  price: number;
  quantityContracts: number;
  notionalUsd: number;
  feesUsd: number;
  realizedPnlUsd?: number;
  timestamp: number;
}

