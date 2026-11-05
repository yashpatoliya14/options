/**
 * Type definitions for the Options Arbitrage Algorithm.
 * Live Delta Exchange (Long / BUY) vs CoinSwitch PRO (Short / SELL)
 */

export type OptionType = "CALL" | "PUT";

export interface ParsedOption {
  exchange: "Delta" | "CoinSwitch";
  symbol: string;
  productId?: number;           // Delta product_id for order placement
  instrumentName?: string;      // CoinSwitch instrument name
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

  // CoinSwitch (Short side: SELL 1 lot = 0.1 unit)
  coinswitchPrice: number;
  coinswitchBid: number;
  coinswitchAsk: number;
  coinswitchIv: number;
  coinswitchSymbol: string;
  coinswitchLots: number;

  // Sizing in underlying units (0.1 BTC, 0.1 ETH, 0.1 XAUT)
  underlyingUnits: number;

  // Spread & Gross Profit
  spreadUsd: number;          // coinswitchPrice - deltaPrice
  spreadPct: number;
  grossProfitUsd: number;     // spreadUsd * underlyingUnits
  grossProfitInr: number;

  // Fee Deductions (Both Delta Exchange and CoinSwitch PRO)
  deltaFeeUsd: number;
  deltaFeeInr: number;
  coinswitchFeeUsd: number;
  coinswitchFeeInr: number;
  totalFeesUsd: number;
  totalFeesInr: number;

  // Fully Calculated Net Profit (Gross Profit - Total Fees)
  netProfitUsd: number;
  netProfitInr: number;

  // Arbitrage Green Mark (🟢): TRUE only if Net Profit > 0 after all fees
  isArb: boolean;
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
  arbOpportunities: StrikeComparison[]; // Only green mark (net profit > 0 after fees)
}

export interface LivePosition {
  exchange: "Delta Exchange" | "CoinSwitch";
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
  coinswitchPositions: LivePosition[];
  deltaUnrealizedUsd: number;
  deltaUnrealizedInr: number;
  coinswitchUnrealizedUsd: number;
  coinswitchUnrealizedInr: number;
  totalUnrealizedUsd: number;
  totalUnrealizedInr: number;
  isFlat: boolean;
  totalPositionsCount: number;
}

export interface LiveWalletInfo {
  exchange: "Delta Exchange" | "CoinSwitch";
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
  coinswitch: LiveWalletInfo;
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

  // CoinSwitch Short leg
  coinswitchSymbol: string;
  coinswitchSide: "sell";
  coinswitchLots: number;
  coinswitchOrderResult?: any;

  // Financial Breakdown
  grossProfitUsd: number;
  totalFeesUsd: number;
  netProfitUsd: number;
  netProfitInr: number;

  status: "pending" | "partial" | "filled" | "failed" | "closed";
  error?: string;
}
