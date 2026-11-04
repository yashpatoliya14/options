/**
 * Type definitions for the Options Arbitrage Bot.
 * Delta Exchange (Long) vs CoinDCX (Short)
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

  // Delta (Long side)
  deltaPrice: number;
  deltaBid: number;
  deltaAsk: number;
  deltaIv: number;
  deltaSymbol: string;
  deltaProductId?: number;

  // CoinDCX (Short side)
  coindcxPrice: number;
  coindcxBid: number;
  coindcxAsk: number;
  coindcxIv: number;
  coindcxSymbol: string;

  // Spread analysis
  spreadUsd: number;        // coindcxPrice - deltaPrice (positive = profitable)
  spreadPct: number;
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
