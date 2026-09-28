# Strategy Explanation (Hinglish)

> BTC options-selling algo — SuperTrend 4h ke basis par directional premium harvest.
> Ye file poori strategy ko simple Hinglish mein samjhati hai, jaisa code abhi
> kaam karta hai.

---

## 1. Ek line mein strategy

BTC ka trend **SuperTrend (4 hour)** se padho. Trend ki direction mein ek **future**
kholo, uspe **stop-loss** lagao (SuperTrend line par), aur ussi direction ka ek
**ATM option becho (sell)** taaki roz ka premium (theta) milta rahe.

- Trend **UP (bullish)** → **LONG future** + **CALL sell**
- Trend **DOWN (bearish)** → **SHORT future** + **PUT sell**

Future hamara hedge hai, option se premium kamate hain. Kabhi bhi naked/dono taraf
ka trade nahi — sirf **ek direction**.

---

## 2. Zaroori concepts (basics)

- **SuperTrend**: ek trend indicator. Line ke upar price = uptrend, niche = downtrend.
  Ye line hi hamara **stop-loss level** banti hai (trend palat gaya to stop lag jaata hai).
- **0DTE option**: same-day expiry wala option. Delta pe BTC daily options **12:00 UTC
  = 5:30 PM IST** par settle hote hain.
- **ATM option**: current price ke sabse paas wale strike ka option.
- **Premium sell karna**: option bech ke paisa aaj hi mil jaata hai; agar option OTM
  reh gaya (worthless), to poora premium hamara profit.

---

## 3. Entry (jab position khaali/flat hai)

Jab koi trade open nahi hai, algo SuperTrend ki direction dekhta hai:

| Trend    | Future        | Stop-loss                | Option        |
|----------|---------------|--------------------------|---------------|
| BULLISH  | LONG 1 future | SuperTrend line ke niche | SELL ATM CALL |
| BEARISH  | SHORT 1 future| SuperTrend line ke upar  | SELL ATM PUT  |

- Future **current price** par khulta hai.
- Stop-loss ek **real resting order** hai jo Delta exchange par rehta hai — matlab
  trend palte hi exchange khud future band kar dega, algo ko har second dekhna nahi padta.
- Option **ATM 0DTE** bikta hai jo aaj 5:30 PM IST (12:00 UTC) par settle hoga.

---

## 4. Stop-loss = SuperTrend line

Stop-loss hamesha **SuperTrend line** par lagta hai (`stop_level_at`):
- Uptrend mein line price ke **niche** hoti hai (long ka stop).
- Downtrend mein line price ke **upar** hoti hai (short ka stop).

Agar SuperTrend refresh nahi hua (line available nahi), to fallback: long ke liye
spot ka **-2%**, short ke liye **+2%**.

---

## 5. Expiry par kya hota hai — 5:30 PM IST (12:00 UTC)

Ye decision **sirf expiry time par** hota hai, intraday nahi. Jab option settle hone
ka time aata hai (`_now() >= opt_settle`), algo dekhta hai future profit mein hai ya nahi:

### (A) Future PROFIT mein hai → CUT ALL + turant naya trade
Bullish mein `spot > entry`, bearish mein `spot < entry`:
1. **Sab kuch band karo**: future close + stop cancel. (Option to abhi 5:30 PM IST par
   khud settle ho chuka hai, isliye alag se kuch buy-back nahi karna padta.)
2. **Turant naya trade lo** (agle loop ka intezaar nahi):
   - Stop-loss = **nearest SuperTrend line**
   - Future = **current price** par
   - **Naya ATM option sell**
3. Naya trade uss samay ke SuperTrend ke hisaab se hota hai — agar trend palat gaya
   to ulti direction mein naya trade khulega (ye sahi trend-following behaviour hai).

### (B) Future PROFIT mein nahi hai → ROLL
Future ko **open hi rakho**, sirf ek **naya option** bech do jiska strike = future ke
**entry price** ke paas (ab OTM ho chuka). Premium harvesting jaari.

> Note: naya bika option agle din 5:30 PM IST par settle hota hai, isliye ye poora
> check **roz ek baar, expiry time par** chalta hai.

---

## 6. Position tracking / reconciliation

- Algo apni position **SQLite (`state.db`)** mein durable save karta hai. Restart hone
  par wahi open trade resume hota hai, do baar trade nahi khulta.
- Har ~30 second mein algo exchange se check karta hai ki future sach mein zinda hai ya nahi
  (`position_size`). Agar aapne **manually Delta pe position band kar di** (ya stop-loss
  lag gaya), to algo samajh jaata hai, state ko flat kar deta hai, aur agle signal par
  naya trade le leta hai.

---

## 7. Run modes (paisa lagne ka control)

`.env` file se control hota hai:

| Mode           | Kaise                                   | Kya hota hai                         |
|----------------|-----------------------------------------|--------------------------------------|
| **DRY-RUN**    | `PLACE_ORDERS=false`                    | Sirf decide + log + Telegram, koi order nahi |
| **TESTNET**    | `PLACE_ORDERS=true`, `USE_TESTNET=true` | Testnet (demo) par real orders       |
| **LIVE**       | `PLACE_ORDERS=true`, `USE_TESTNET=false`| **Asli paisa** — real orders         |

> ⚠️ Demo se Live switch karte time `state.db` ka purana demo position resume ho sakta
> hai. Live chalane se pehle `state.db` ka open row close kar dena (flat start) zaroori hai.

---

## 8. Zaroori settings (.env)

- `PLACE_ORDERS`, `USE_TESTNET` — mode (upar wali table).
- `DELTA_API_KEY` / `DELTA_API_SECRET` — Delta credentials.
- `TRADE_QTY` — kitne contracts (default 1).
- `TELEGRAM_ENABLED` / `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` — alerts.
- SuperTrend settings `src/config.py` mein: **4h timeframe**, ATR 15, multiplier 3.0.

---

## 9. Dhyan rakhne wali baatein (risks)

- **4h SuperTrend zyada whipsaw karta hai** 8h ke muqable (~2x flips). Agar false signals
  se nuksaan ho, to `st_multiplier` ko 4.0 kar dena (4h ko 8h jaisa stable bana deta hai).
- Stop-loss exchange par rehta hai — lekin option leg stop se protected nahi hoti,
  wo apni expiry (5:30 PM IST) tak short rehti hai.
- Live mode mein har order asli paisa hai. Pehle testnet/dry-run par test karo.
- Time hamesha **UTC** mein sochna: 12:00 UTC = 5:30 PM IST (settlement).

---

## 10. Flow ka short summary

```
Flat?  → SuperTrend padho → direction mein future + ST-line stop + ATM option sell
Open?  → 5:30 PM IST (expiry) tak wait (stop exchange sambhaal raha hai)
Expiry → future profit?  → CUT ALL + turant naya trade (ST-line stop, current price, ATM sell)
                          → nahi?  → ROLL (future rakho, naya option entry-strike par sell)
Beech mein manual close / stop lag gaya? → reconcile → flat → agle signal par naya trade
```
