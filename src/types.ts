/**
 * Type definitions for the Options IV Arbitrage Algorithm.
 */

export type OptionType = "CALL" | "PUT";

export interface ParsedOption {
  exchange: "Delta Exchange" | "CoinSwitch";
  symbol: string;
  strike: number;
  optionType: OptionType;
  markPrice: number;
  bestBid: number;
  bestAsk: number;
  ivPct: number;
  settleDtUtc: Date;
  settleDtIst: Date;
  settleTimeStr: string;
  expiryLabel: string;
  raw?: any;
}

export interface ArbitrageRow {
  strike: number;
  optionType: OptionType;
  category: "ATM" | "OTM Call" | "OTM Put";
  coinswitchPrice: number;
  coinswitchPriceInr: number;
  coinswitchBid: number;
  coinswitchAsk: number;
  coinswitchIv: number;
  deltaPrice: number;
  deltaPriceInr: number;
  deltaBid: number;
  deltaAsk: number;
  deltaIv: number;
  diff: number;
  diffPct: number;
  ivDiff: number;
  isArbitrage: boolean;
  targetDateStr: string;
  deltaExpiry: string;
  coinswitchExpiry: string;
  // Lot & Margin calculations
  tradeLotBtc: number;
  deltaContracts: number;
  requiredMarginUsd: number;
  requiredMarginInr: number;
  potentialProfitUsd: number;
  potentialProfitInr: number;
}

export interface ScanResult {
  spotPrice: number;
  targetDate: Date;
  cycleDesc: string;
  atmStrike: number;
  atmCall?: ArbitrageRow;
  atmPut?: ArbitrageRow;
  otmCalls: ArbitrageRow[];
  otmPuts: ArbitrageRow[];
  allRows: ArbitrageRow[];
  arbitrageOpportunities: ArbitrageRow[];
  tradeLotBtc: number;
}

export interface WalletBalanceInfo {
  exchange: "Delta Exchange" | "CoinSwitch";
  balanceUsd: number;
  balanceInr: number;
  availableUsd: number;
  availableInr: number;
  details?: any;
  error?: string;
}

export interface BalanceResult {
  deltaUsd: number;
  deltaInr: number;
  deltaPct: number;
  deltaError?: string;
  coinswitchUsd: number;
  coinswitchInr: number;
  coinswitchPct: number;
  coinswitchError?: string;
  totalUsd: number;
  totalInr: number;
  diffUsd: number;
  diffInr: number;
  imbalancePct: number;
  isImbalanced: boolean;
  hasZeroBalance: boolean;
  zeroBalanceExchanges: string[];
  thresholdPct: number;
  transferUsd: number;
  transferInr: number;
  fromExchange: string;
  toExchange: string;
  // Margin check for TRADE_LOT_BTC
  tradeLotBtc: number;
  deltaContracts: number;
  estimatedMarginPerExchangeUsd: number;
  canExecuteTrade: boolean;
}
