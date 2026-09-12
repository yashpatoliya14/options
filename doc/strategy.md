# Strategy Guide

## Signal

The active directional mode uses a Supertrend signal on closed 1-hour BTC candles:

- Supertrend turns bullish and holds for `min_trend_bars` bars: create a `bull` signal.
- Supertrend turns bearish and holds for `min_trend_bars` bars: create a `bear` signal.
- The EMA trend filter can require price to be above the EMA for bullish signals or below it for bearish signals.
- ADX, RSI, ATR, volatility, session, and entry-timing filters are optional and controlled by `StrategyParams`.

Legacy EMA crossover detection remains available for compatibility, but directional configuration uses Supertrend.

## Spread Construction

- Bullish signal: open a bull put credit spread.
- Bearish signal: the active configuration uses a bear call-credit spread: sell the lower-strike call and buy the higher-strike call. The screenshot-style higher-put sell/lower-put buy spread is a bullish/neutral put credit, not a bearish payoff.
- Select an eligible expiry according to `expiry_selection`, then choose strikes using the configured strike method.
- Liquidity checks, credit bounds, spread width, and optional spread scoring must pass before an order is sent.
- Credit spreads must meet `credit / (spread_width - credit) >= min_credit_risk_ratio`; the active setting is `0.90`, approximately 0.9:1 reward-to-maximum-loss.
- The active `bear_structure: call_credit` matches the bearish payoff: short lower call, long higher call, positive entry credit. `put_credit` remains available for bullish/neutral put-credit trades.

## Position Management

The engine marks a spread as `short_put - long_put` and evaluates:

- Profit target: unrealized profit reaches `take_profit_pct` of the relevant risk/profit amount; the active setting captures 50%.
- Stop loss: disabled by default with `stop_loss_enabled: false`; the position is not force-closed by an adverse mark.
- Reversal: an opposite Supertrend signal closes the position only after `reversal_profit_capture_pct` (50%) of the entry profit has been captured.
- Expiry: `early_exit_minutes: 0` and `max_hold_hours: 0` keep positions open until expiry unless the profit target or profit-gated reversal closes them.
- Optional Greek exits can close positions based on net delta or remaining DTE.
- Cooldown prevents immediate re-entry after a close.

## Risk and Execution

Position sizing can be fixed quantity or risk-based. Set `TRADE_QTY` in `.env` to control live and terminal-backtest lots directly; `1` means one contract per spread leg. The testnet demo intentionally stays at one lot for safety. Slippage, bid/ask fills, settlement fees, liquidity limits, and execution delay are configurable. Exchange stops are disabled by default and should only be enabled after the exchange behavior is verified.

Backtest option chains are usually Black-Scholes reconstructions with configured IV and rates. After correcting expiry handling, payoff direction, and closest-ratio selection, the latest run produced 103 trades, 73 wins, 30 losses, and simulated P&L of `-$1,376.23`. The win rate is 70.87%, but average losses are much larger than average wins, so this configuration is still not profitable and must not be used live.
