# Polybot - Polymarket BTC Binary Options Trading Bot

## Project Overview

This bot trades Polymarket's 15-minute BTC binary options markets. The goal is to buy both YES and NO shares at prices that sum to less than $1.00, guaranteeing profit regardless of outcome.

```
Pair Cost = avg_price_YES + avg_price_NO

If Pair Cost < $1.00 → Guaranteed profit
If Pair Cost > $1.00 → Guaranteed loss
```

---

## Current Strategy: Triple Gate

### Core Concept

Three pricing gates determine the maximum bid price. Final price = min(P_acct, P_mkt, Cap_exec).

### Gate 1: P_acct (Accountant) - Position-Aware Max

Ensures no portfolio loss based on current position.

**Heavy side (or balanced):**
```python
P_acct = 1000 - avg_opposite - margin
```

**Light side (need to catch up):**
```python
shares_needed = heavy_qty - light_qty
P_acct = (heavy_qty * (1000 - avg_heavy) - light_cost) / shares_needed
```

The light side formula allows higher bids because new shares dilute into a larger final position.

### Gate 2: P_mkt (Market Maker) - Replacement Cost with Skew

Anchors to what it would cost to replace position, adjusted for inventory.

```python
anchor = 1000 - ask_opposite - margin
skew = GAMMA * net_position * 1000  # Positive when light, negative when heavy
P_mkt = anchor + skew
```

### Gate 3: Cap_exec (Execution Cap) - Maker vs Taker

Controls whether we cross the spread.

**Heavy side:** `Cap_exec = ask - 10` (stay 1c below ask, maker only)
**Light side:** `Cap_exec = ask + SLIPPAGE_TOL` (can cross spread to catch up)

### Final Price

```python
p_final = min(P_acct, P_mkt, Cap_exec)
p_final = max(MIN_PRICE, min(990, p_final))  # Clamp to valid range
```

---

## Current Parameters (config.py)

```python
# Position limits
MAX_POSITION = 75          # Max net exposure per side

# Order sizing
BASE_SIZE = 10             # Order size when neutral (shares)
MIN_ORDER_SIZE = 5         # Polymarket API minimum

# Pricing
BASE_MARGIN_TICKS = 15     # 1.5c minimum profit margin
GAMMA = 0.001              # Skew sensitivity
MAX_SKEW_TICKS = 100       # 10c max price shift

# Ladder
LADDER_DEPTH = 5           # Number of rungs per side
MIN_PRICE = 100            # 10c floor

# Execution
SLIPPAGE_TOL_TICKS = 20    # 2c max spread crossing when light
HYSTERESIS = 0.50          # 50% size tolerance before shrinking

# Safety
PROFIT_LOCK_MIN = 10.0     # Stop trading at $10 guaranteed profit
CIRCUIT_BREAKER_USD = 200  # Emergency stop
```

---

## Order Sizing

Linear scaling based on net position:

```python
net_pos = Qy - Qn  # Positive = heavy YES, negative = heavy NO

if abs(net_pos) >= MAX_POSITION:
    return 0  # Stop placing on heavy side

scalar = 1.0 - (abs(net_pos) / MAX_POSITION)  # 1.0 at neutral, 0.0 at max
target_size = BASE_SIZE * scalar
```

**Example:** At net_pos = 37 with MAX_POSITION = 75:
- scalar = 1 - 37/75 = 0.51
- target_size = 10 * 0.51 = 5.1 shares

---

## Diff Engine (Reconciliation)

On every price update or fill, reconcile current orders with ideal ladder.

### Phase 1: Cancel Stale
Remove orders at prices not in the ideal ladder.

### Phase 2: Place/Stack/Shrink/Hold
For each price in ideal ladder:
- **PLACE**: No order exists → place new order (if size >= MIN_ORDER_SIZE)
- **STACK**: Current < target → add difference (if diff >= MIN_ORDER_SIZE)
- **SHRINK**: Current > target * 1.5 → cancel and replace
- **HOLD**: Within tolerance → do nothing

---

## Polymarket API Limits

Discovered through testing:

| Limit | Value | Error Message |
|-------|-------|---------------|
| Min order size | 5 shares | "Size (X) lower than the minimum: 5" |
| Batch size | 15 orders | Per API call |
| Price precision | 0.001 ($0.001) | 10 ticks = 1 cent |
| Size precision | 0.01 shares | 2 decimal places |

---

## Key Files

| File | Purpose |
|------|---------|
| `live_trade.py` | Main trading script with profit lock |
| `execution/order_manager.py` | Triple Gate pricing + diff engine |
| `execution/order_tracker.py` | Tracks standing orders (supports stacking) |
| `execution/real_executor.py` | API calls with error logging |
| `config.py` | All parameters |

---

## Running

```bash
cd /root/polybot
source venv/bin/activate
python live_trade.py              # Run indefinitely
python live_trade.py -n 1         # Run for 1 market
```

---

## Results from Live Testing (2026-01-17)

### Problem: Fast Market Crashes

When BTC crashes, the bot accumulates one side rapidly before it can react.

**Example from log (08:45:37-08:45:38):**
```
08:45:37 | YES 10.2 @ $0.49 | Pos: Y:20 N:11
08:45:37 | YES 10.2 @ $0.50 | Pos: Y:30 N:11
08:45:37 | YES 10.2 @ $0.48 | Pos: Y:41 N:11
08:45:37 | YES 10.2 @ $0.46 | Pos: Y:51 N:11
08:45:38 | YES 10.2 @ $0.43 | Pos: Y:71 N:11
08:45:38 | YES 10.2 @ $0.43 | Pos: Y:81 N:11
08:45:38 | YES 10.2 @ $0.42 | Pos: Y:91 N:11
```

**What happened:**
1. Bot had 5 ladder rungs × 10 size = 50 shares exposed on YES side
2. BTC crashed → everyone sold YES → price dropped 50c → 42c in 1 second
3. All 5 rungs filled before bot could cancel
4. Ended with 91 YES vs 11 NO (80 share imbalance)
5. Final position: Y:121 N:11, MinPnL: -$49

**Root cause:**
- Ladder exposure = LADDER_DEPTH × BASE_SIZE = 50 shares
- In a fast-moving market, entire ladder fills instantly
- MAX_POSITION limit only kicks in AFTER fills happen
- Orders already placed will fill regardless of position

### Problem: Pair Cost > $1.00

When one side crashes, P_mkt anchor formula works against us:
```
P_mkt for NO = 1000 - ask_yes - margin
```

If YES ask drops from 50c to 30c, P_mkt for NO goes from 35c to 55c. This allows HIGHER NO bids exactly when we should be cautious.

**From log:**
- YES crashed to ~30c average
- Bot filled NO at 68c, 70c, 71c
- Pair cost = 30c + 70c = $1.00+ (guaranteed loss)

---

## Unsolved Problems

1. **Exposure per side** - With 5 rungs × 10 shares, 50 shares can fill in 1 second during a crash. No time to react.

2. **P_mkt anchor in trending market** - Replacement cost formula makes the OPPOSITE side look cheaper when one side crashes. This is backwards.

3. **No volatility detection** - Bot doesn't know when market is moving fast. Keeps placing orders during crashes.

4. **Imbalance accumulates faster than it corrects** - Size scaling reduces new orders, but doesn't help with orders already placed.

---

## What We Learned

1. **MIN_ORDER_SIZE = 5** - Polymarket rejects orders < 5 shares
2. **Place-and-hold has risk** - Queue priority is good, but exposed orders fill during crashes
3. **P_mkt formula is flawed** - Replacement cost anchor increases when opposite side crashes
4. **Profit lock works** - Implemented and tested
5. **Stacking works** - Can add to existing orders without losing queue priority
6. **Error logging is essential** - Added API error messages to diagnose rejections

---

## Possible Improvements (Not Implemented)

1. **Reduce exposure** - Fewer rungs, smaller size (3 × 5 = 15 shares max)
2. **Volatility circuit breaker** - If price moves >5c in 10s, cancel everything
3. **Fix P_mkt anchor** - Don't increase opposite bid when one side crashes
4. **Wider spacing** - 2c instead of 1c so crashes don't sweep entire ladder
5. **Accept some losses** - Goal is to win over many markets, not every market

---

## LBV Research

From analysis of successful bot "livebreathevolatility":

| Observation | Evidence |
|-------------|----------|
| Order size: ~15 shares | 60%+ of fills are 14.8-15.0 |
| Spacing: 1c | Consecutive fills at adjacent prices |
| Tolerates high imbalance | Ended market with 277 share imbalance, still profitable |
| Uses profit lock | Stopped trading 3+ min before market end |

**Key insight:** LBV tolerates large imbalances and relies on pair cost formula rather than trying to stay balanced. They probably lose on crash markets too, but win on calm markets.
