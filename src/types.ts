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
}

export interface WalletBalanceInfo {
  exchange: "Delta Exchange" | "CoinSwitch";
  isSimulated: boolean;
  balanceUsd: number;
  balanceInr: number;
  details?: any;
  error?: string;
}

export interface BalanceResult {
  deltaUsd: number;
  deltaInr: number;
  deltaPct: number;
  deltaIsSimulated: boolean;
  coinswitchUsd: number;
  coinswitchInr: number;
  coinswitchPct: number;
  coinswitchIsSimulated: boolean;
  totalUsd: number;
  totalInr: number;
  diffUsd: number;
  diffInr: number;
  imbalancePct: number;
  isImbalanced: boolean;
  thresholdPct: number;
  transferUsd: number;
  transferInr: number;
  fromExchange: string;
  toExchange: string;
}

