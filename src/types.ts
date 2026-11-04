/**
 * Type definitions for the Options Arbitrage Algorithm.
 * Live Delta Exchange (Long) vs CoinDCX (Short)
 */

export type OptionType = "CALL" | "PUT";

export interface ParsedOption {
  exchange: "Delta" | "CoinDCX";
  symbol: string;
  productId?: number;           // Delta product_id for order placement
  instrumentName?: string;      // CoinDCX instrument name
  underlying: string;
  strike: number;
  optionType: OptionType;
  markPrice: number;            // in USD
  bestBid: number;
  bestAsk: number;
  ivPct: number;
  expiryDate: string;           // YYYY-MM-DD
  expiryLabel: string;
  settleDtUtc: Date;
  raw?: any;
}

export interface StrikeComparison {
  strike: number;
  optionType: OptionType;
  category: "ATM" | "OTM";
  expiryDate: string;
  expiryLabel: string;

  // Delta (Long side: BUY 10 lots = 0.1 unit)
  deltaPrice: number;
  deltaBid: number;
  deltaAsk: number;
  deltaIv: number;
  deltaSymbol: string;
  deltaProductId?: number;
  deltaLots: number;

  // CoinDCX (Short side: SELL 1 lot = 0.1 unit)
  coindcxPrice: number;
  coindcxBid: number;
  coindcxAsk: number;
  coindcxIv: number;
  coindcxSymbol: string;
  coindcxLots: number;

  // Sizing in underlying units (0.1 BTC, 0.1 ETH, 0.1 XAUT)
  underlyingUnits: number;

  // Spread analysis: CoinDCX (short) - Delta (long)
  spreadUsd: number;        // coindcxPrice - deltaPrice
  spreadPct: number;
  potentialProfitUsd: number;
  potentialProfitInr: number;
  isSuggested: boolean;     // short premium >= long premium
}

export interface OptionChainResult {
  underlying: string;
  spotPrice: number;
  atmStrike: number;
  expiryDate: string;
  expiryLabel: string;
  atmCalls: StrikeComparison[];
  atmPuts: StrikeComparison[];
  otmCalls: StrikeComparison[];
  otmPuts: StrikeComparison[];
  allStrikes: StrikeComparison[];
  suggestedTrades: StrikeComparison[];
}

export interface LivePosition {
  exchange: "Delta Exchange" | "CoinDCX";
  symbol: string;
  side: "LONG" | "SHORT" | string;
  size: number;
  entryPrice: number;
  markPrice: number;
  unrealizedPnlUsd: number;
  unrealizedPnlInr: number;
  leverage?: number;
  raw?: any;
}

export interface NetPnlReport {
  timestamp: Date;
  deltaPositions: LivePosition[];
  coindcxPositions: LivePosition[];
  deltaUnrealizedUsd: number;
  deltaUnrealizedInr: number;
  coindcxUnrealizedUsd: number;
  coindcxUnrealizedInr: number;
  totalUnrealizedUsd: number;
  totalUnrealizedInr: number;
  isFlat: boolean;
  totalPositionsCount: number;
}

export interface LiveWalletInfo {
  exchange: "Delta Exchange" | "CoinDCX";
  walletName?: string;
  balanceUsd: number;
  balanceInr: number;
  availableUsd: number;
  availableInr: number;
  lockedMarginInr?: number;
  lockedMarginUsd?: number;
  currencies: {
    symbol: string;
    total: number;
    available: number;
  }[];
  error?: string;
}

export interface CombinedWalletReport {
  timestamp: Date;
  delta: LiveWalletInfo;
  coindcx: LiveWalletInfo;
  totalBalanceUsd: number;
  totalBalanceInr: number;
  totalAvailableUsd: number;
  totalAvailableInr: number;
}

export interface TradeExecution {
  id: string;
  timestamp: Date;
  underlying: string;
  strike: number;
  optionType: OptionType;
  expiryDate: string;

  // Delta Long leg
  deltaSymbol: string;
  deltaProductId?: number;
  deltaSide: "buy";
  deltaLots: number;
  deltaOrderResult?: any;

  // CoinDCX Short leg
  coindcxSymbol: string;
  coindcxSide: "sell";
  coindcxLots: number;
  coindcxOrderResult?: any;

  status: "pending" | "partial" | "filled" | "failed" | "closed";
  error?: string;
}
