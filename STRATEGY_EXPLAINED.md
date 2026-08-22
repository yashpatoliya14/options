# BTC Options Arbitrage Strategy — Complete Guide
## Put-Call Parity Arbitrage on Delta Exchange

---

## Table of Contents
1. [What is This Strategy?](#1-what-is-this-strategy)
2. [The Core Theory: Put-Call Parity](#2-the-core-theory-put-call-parity)
3. [Strategy #1: Conversion & Reversal](#3-strategy-1-conversion--reversal)
4. [Strategy #2: Box Spread](#4-strategy-2-box-spread)
5. [Real-World Example with Numbers](#5-real-world-example-with-numbers)
6. [Fee & Slippage Model](#6-fee--slippage-model)
7. [The Algorithm Logic](#7-the-algorithm-logic)
8. [Risk Management](#8-risk-management)
9. [How to Run the Live Algo](#9-how-to-run-the-live-algo)

---

## 1. What is This Strategy?

This is a **market-neutral arbitrage** strategy. It doesn't bet on whether BTC goes up or down.
Instead, it exploits **pricing mistakes** between related instruments:

- **BTC Call Option** (right to BUY BTC at a fixed price)
- **BTC Put Option** (right to SELL BTC at a fixed price)
- **BTC Future/Perpetual** (agreement to buy/sell BTC at a future date)

When these three instruments are **mispriced relative to each other**, we can lock in a
**risk-free profit** by trading all three simultaneously.

> **Think of it like this:**
> If you can buy a Rs.100 note for Rs.95 by combining three cheaper items,
> you buy them all and pocket the Rs.5 difference — guaranteed.

---

## 2. The Core Theory: Put-Call Parity

### The Formula

```
Call Price - Put Price = Future Price - Strike Price
```

Or equivalently:
```
C - P = F - K
```

Where:
- **C** = Call option price (same strike, same expiry)
- **P** = Put option price (same strike, same expiry)
- **F** = Futures/perpetual price
- **K** = Strike price of both options

### Why Does This Hold?

Because a **Call + Strike = Put + Future** creates the same payoff at expiry:

| Scenario at Expiry | Call + Cash(K) | Put + Future |
|---|---|---|
| BTC = Rs.70,000 (above K=65,000) | 70,000 - 65,000 + 65,000 = **70,000** | 0 + 70,000 = **70,000** |
| BTC = Rs.60,000 (below K=65,000) | 0 + 65,000 = **65,000** | 65,000 - 60,000 + 60,000 = **65,000** |

Both sides ALWAYS give the same result. So they MUST cost the same today.
If they don't → **ARBITRAGE OPPORTUNITY!**

### The "Gap"

We define:
```
Gap = (C - P) - (F - K)
```

- If Gap = 0 → No arbitrage (market is efficient)
- If Gap > 0 → Call is overpriced → **REVERSAL** trade
- If Gap < 0 → Put is overpriced → **CONVERSION** trade

---

## 3. Strategy #1: Conversion & Reversal

### CONVERSION (when Gap < 0, i.e., synthetic future is cheap)

The "synthetic future" (Call - Put + Strike) is **cheaper** than the actual future.

**What we do:**
```
BUY  Call   (buy the cheap leg)
SELL Put    (sell the cheap leg)
SELL Future (sell the expensive actual future)
```

**Profit = |Gap| - Fees - Slippage**

### REVERSAL (when Gap > 0, i.e., synthetic future is expensive)

The "synthetic future" is **more expensive** than the actual future.

**What we do:**
```
SELL Call   (sell the expensive leg)
BUY  Put   (buy the expensive leg)  
BUY  Future (buy the cheap actual future)
```

**Profit = |Gap| - Fees - Slippage**

### Visual Summary

```
                    Gap > 0 (Call too expensive)
                    ┌─────────────────────────┐
                    │      REVERSAL            │
                    │  Sell Call + Buy Put      │
                    │  + Buy Future             │
                    └─────────────────────────┘
                    
    ◄───────────── Gap = 0 (Fair Price) ──────────────►
    
                    ┌─────────────────────────┐
                    │      CONVERSION          │
                    │  Buy Call + Sell Put      │
                    │  + Sell Future            │
                    └─────────────────────────┘
                    Gap < 0 (Put too expensive)
```

---

## 4. Strategy #2: Box Spread

A box spread uses **two strikes** (K1 and K2) and combines:

```
Buy  Call at K1  +  Sell Call at K2    (Bull Call Spread)
Sell Put  at K1  +  Buy  Put  at K2   (Bear Put Spread)
```

### Theory

The box spread should always pay exactly **(K2 - K1)** at expiry, regardless of BTC price.

So the **fair cost today** = K2 - K1

```
Box Value = (Call_K1 - Call_K2) + (Put_K2 - Put_K1)
Edge = Box Value - (K2 - K1)
```

- If Edge > 0 → Box is overpriced → **SELL the box**
- If Edge < 0 → Box is underpriced → **BUY the box**

### Example

```
K1 = 64,000    K2 = 66,000
Fair Value = 66,000 - 64,000 = $2,000

Call at K1 = $1,800    Call at K2 = $700
Put  at K1 = $650      Put  at K2 = $1,750

Box Value = (1800 - 700) + (1750 - 650) = 1100 + 1100 = $2,200

Edge = 2,200 - 2,000 = $200 (overpriced → sell box for guaranteed $200 profit)
```

---

## 5. Real-World Example with Numbers

### Setup
```
BTC Future Price (F)  = $65,050
Strike Price (K)      = $65,000
Call Price (C)         = $1,250
Put Price (P)          = $1,180
```

### Step 1: Calculate the Gap
```
Gap = (C - P) - (F - K)
Gap = (1,250 - 1,180) - (65,050 - 65,000)
Gap = 70 - 50
Gap = +$20     ← POSITIVE → REVERSAL signal
```

### Step 2: Calculate Costs

**Delta Exchange Fees (per 1 BTC):**
```
Option Leg 1 (Call):  65,050 × 0.0003 = $19.52
Option Leg 2 (Put):   65,050 × 0.0003 = $19.52
Future Leg:           65,050 × 0.0005 = $32.53
─────────────────────────────────────────────
Total Fees:                              $71.56
```

**Slippage (estimated):**
```
Option Leg 1:  $3.00
Option Leg 2:  $3.00
Future Leg:    $1.00
─────────────────────
Total Slippage: $7.00
```

### Step 3: Check if Tradable
```
Gross Edge:   $20.00
Total Costs:  $71.56 + $7.00 = $78.56
Net Edge:     $20.00 - $78.56 = -$58.56  ← NEGATIVE, NOT TRADABLE!
```

**This trade is NOT profitable** because the gap is too small to cover costs.

### When IS it Tradable?

The gap needs to be > ~$79 (per 1 BTC) to be profitable. Let's say:

```
Call Price = $1,350  (stale quote, wider spread)
Put Price  = $1,180
Future     = $65,050

Gap = (1,350 - 1,180) - (65,050 - 65,000)
Gap = 170 - 50 = +$120

Net Edge = $120 - $78.56 = +$41.44  ← PROFITABLE!
```

### Step 4: Scale to Contract Size

With Rs.1,000 margin (~$12), we trade **0.001 BTC contracts**:
```
Profit per trade = $41.44 × 0.001 = $0.04144
In INR           = $0.04144 × 83.50 = Rs.3.46
```

Small per trade, but with **~1,400 trades/month**, it adds up!
```
Monthly Profit ≈ 1,400 × Rs.3.46 ≈ Rs.4,844
```

---

## 6. Fee & Slippage Model

### Delta Exchange Fee Schedule

| Component | Rate | Example (BTC @ $65,000) |
|---|---|---|
| Option Taker Fee | 0.03% (3 bps) | $19.50 per leg |
| Option Maker Fee | 0.03% (3 bps) | $19.50 per leg |
| Future Taker Fee | 0.05% (5 bps) | $32.50 per leg |
| Settlement Fee (ITM) | 0.015% (1.5 bps) | $9.75 at expiry |

### Per-Trade Cost (Conversion/Reversal)
```
2 Option Legs × $19.50 = $39.00  (fees)
1 Future Leg  × $32.50 = $32.50  (fees)
2 Option Legs × $3.00  = $6.00   (slippage)
1 Future Leg  × $1.00  = $1.00   (slippage)
──────────────────────────────────────────
TOTAL COST per 1 BTC   = $78.50
TOTAL COST per 0.001   = $0.0785
```

### What This Means

Out of every dollar of gross edge:
- **~65%** goes to exchange fees
- **~6%** goes to slippage
- **~29%** is your net profit

This is typical for arbitrage — thin margins, high volume.

---

## 7. The Algorithm Logic

### Signal Detection (Every 5 Minutes)

```
┌─────────────────────────────────────────┐
│  1. GET latest prices:                  │
│     - Call price at Strike K            │
│     - Put price at Strike K             │
│     - Future/Perpetual price            │
│                                         │
│  2. CALCULATE gap:                      │
│     gap = (C - P) - (F - K)            │
│                                         │
│  3. CALCULATE costs:                    │
│     fees = F × (3+3+5) bps             │
│     slip = $3 + $3 + $1 = $7           │
│     total_cost = fees + slip            │
│                                         │
│  4. CHECK profitability:                │
│     net_edge = |gap| - total_cost       │
│     if net_edge > 0:                    │
│       → TRADE!                          │
│                                         │
│  5. EXECUTE:                            │
│     if gap > 0: REVERSAL                │
│     if gap < 0: CONVERSION              │
└─────────────────────────────────────────┘
```

### Position Management

```
┌─────────────────────────────────────────┐
│  RISK LIMITS:                           │
│  - Max 5 open positions simultaneously  │
│  - Max 0.001 BTC per position           │
│  - Max 50 trades per day                │
│  - Stop if portfolio drops below Rs.500 │
│  - Cool-down: 5 min between trades      │
│                                         │
│  EXIT STRATEGY:                         │
│  - Hold until expiry (parity converges) │
│  - OR close when gap reverses to 0      │
│  - OR close after 24 hours max hold     │
└─────────────────────────────────────────┘
```

---

## 8. Risk Management

### What Can Go Wrong?

| Risk | Description | Mitigation |
|---|---|---|
| **Execution Risk** | Can't fill all 3 legs simultaneously | Use IOC orders, check fills |
| **Liquidity Risk** | Wide spreads eat into edge | Skip signals with edge < 2× cost |
| **API Downtime** | Delta Exchange goes offline | Auto-pause, reconnect logic |
| **Margin Call** | Position goes against you temporarily | Keep 50% margin buffer |
| **Pin Risk** | Option expires exactly at-the-money | Close positions before expiry |
| **Model Risk** | Fees change, slippage is higher | Conservative cost estimates |

### Position Sizing

```
Available Margin    = Rs.1,000
USD Equivalent      = ~$12
Leverage (10x)      = $120 notional
BTC @ $65,000       = 0.001846 BTC max

We use 0.001 BTC = $65 notional
Margin Required     = $65 / 10 = $6.50 (Rs.543)
Margin Buffer       = Rs.1,000 - Rs.543 = Rs.457 (safe)
```

---

## 9. How to Run the Live Algo

### Prerequisites

1. **Delta Exchange Account** with API key and secret
2. **Fund your account** with at least $15-20 USDT
3. **Install dependencies:**
   ```
   pip install requests websocket-client pandas numpy
   ```

### Configuration

Edit `live_algo.py` and set:
```python
API_KEY    = "your_delta_api_key"
API_SECRET = "your_delta_api_secret"
```

### Running

```bash
# Dry run (paper trading, no real orders):
python live_algo.py --mode dry_run

# Live trading:
python live_algo.py --mode live
```

### Important Modes

| Mode | Description |
|---|---|
| `dry_run` | Logs signals but doesn't place real orders |
| `live` | Places real orders on Delta Exchange |

### Monitoring

The algo generates:
- **Console output** with real-time signal alerts
- **`algo_log.csv`** with every signal and trade
- **`positions.json`** with current open positions

---

## Glossary

| Term | Meaning |
|---|---|
| **Arbitrage** | Risk-free profit from price differences |
| **Put-Call Parity** | Mathematical relationship: C - P = F - K |
| **Conversion** | Buy Call + Sell Put + Sell Future (when synthetic is cheap) |
| **Reversal** | Sell Call + Buy Put + Buy Future (when synthetic is expensive) |
| **Box Spread** | 4-leg trade across 2 strikes for fixed payoff |
| **Edge** | The profit opportunity (gap minus costs) |
| **Slippage** | Extra cost from not getting the exact price you want |
| **BPS** | Basis Points. 1 bps = 0.01%. 100 bps = 1% |
| **Notional** | The total value of the position (e.g., 0.001 × $65,000 = $65) |
| **Margin** | The deposit required to hold a leveraged position |

---

## Disclaimer

> **This is for educational purposes only.** Past backtest results (even with realistic
> simulation) do not guarantee future profits. Real markets have additional risks including
> but not limited to: exchange downtime, API rate limits, flash crashes, regulatory changes,
> and counterparty risk. Always start with small amounts and paper trade first.

---

*Generated by BTC Options Arbitrage Backtester — August 2026*
