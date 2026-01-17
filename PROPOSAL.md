# Polybot Strategy Specification: Hybrid Inventory Market Maker

## 1. Core Philosophy

This bot combines **Accounting-Based Risk Management** with **Stochastic Market Making**.

* **Dual Pricing Model:**
    1.  **The Accountant (Past Edge):** Calculates the maximum affordable price to guarantee total portfolio profit based on historical entry prices.
    2.  **The Market Maker (Future Edge):** Calculates the competitive price based on current market replacement cost and inventory skew.
* **The "Triple Gate":** The final bid is the **minimum** of the Accountant's Limit, the Market Maker's Target, and the Execution Cap.
* **Execution:** Uses "Sweep-and-Post" behavior when desperate (crossing the spread) and "Place-and-Hold" when passive.

---

## 2. Configuration (Tunable Parameters)

| Parameter | Type | Value | Description |
| :--- | :--- | :--- | :--- |
| `MAX_POSITION` | `Int` | `75` | Maximum net exposure. Set closer to `IMBALANCE_HARD` for guaranteed profit mode. |
| `BASE_SIZE` | `Int` | `20` | Standard order size (shares). |
| `BASE_MARGIN` | `Float` | `0.015` | Minimum profit margin ($0.015). |
| `GAMMA` | `Float` | `0.001` | **Aggressive Skew.** Sensitivity to imbalance.<br>*(50 shares × 0.001 = 5¢ shift)* |
| `MAX_SKEW` | `Float` | `0.10` | Maximum allowed price shift from the anchor. |
| `LADDER_DEPTH` | `Int` | `10` | Number of price levels to quote. Increased to catch volatility. |
| `SLIPPAGE_TOL` | `Float` | `0.02` | Max ticks to cross the spread when desperate. |
| `HYSTERESIS` | `Float` | `0.50` | Size tolerance (50%) to prevent churning. |
| `MIN_TICK` | `Float` | `0.01` | Minimum price increment. |

---

## 3. The Pricing Algorithm (Per Side)

This logic runs independently for **YES** and **NO**.

### Step A: Calculate Net Position
$$Net\_Pos = My\_Shares_{ThisSide} - My\_Shares_{OppositeSide}$$

### Step B: Calculate "Affordable Max" ($P_{acct}$)
*The Ceiling: Ensures we never lock in a portfolio loss.*

* **If Light/Short (Need to Buy):**
    $$P_{acct} = \frac{HeavyQty \times (1.00 - AvgHeavy) - LightCost}{SharesNeeded}$$
* **If Heavy/Neutral (Selling/Passive):**
    $$P_{acct} = 1.00 - Avg_{Opposite} - BASE\_MARGIN$$

### Step C: Calculate "Market Target" ($P_{mkt}$)
*The Target: The competitive price we "want" to pay based on current liquidity.*

1.  **Anchor (Replacement Cost):**
    $$Anchor = 1.00 - Ask_{Opposite} - BASE\_MARGIN$$
2.  **Inventory Skew:**
    $$Skew = \text{clamp}(Net\_Pos \times GAMMA, -MAX\_SKEW, MAX\_SKEW)$$
3.  **Raw Target:**
    $$P_{mkt} = Anchor - Skew$$

### Step D: Determine Execution Cap ($Cap_{exec}$)
*The Governor: Controls spread crossing.*

* **If Light/Short (Aggressive):**
    $$Cap_{exec} = Ask_{ThisSide} + SLIPPAGE\_TOL$$
* **If Heavy/Neutral (Passive):**
    $$Cap_{exec} = Ask_{ThisSide} - 0.01$$

### Step E: The Triple Gate (Final Price)
$$P_{final} = \min(P_{acct}, P_{mkt}, Cap_{exec})$$

* **Hard Limits:** Clamp $P_{final}$ between $0.02$ and $0.99$.

---

## 4. The Sizing Algorithm

Determine order size based on inventory "hunger."

### Formula
$$Scalar = 1.0 - \left( \frac{Net\_Pos}{MAX\_POSITION} \right)$$
$$Target\_Size = \text{floor}(BASE\_SIZE \times \text{clamp}(Scalar, 0.0, 2.0))$$

---

## 5. Ladder Construction

Construct the "Ideal State" starting from the Triple Gate price.

1.  **Top Rung:** Price = $P_{final}$, Size = $Target\_Size$.
    * *Note:* If $P_{final} \ge Ask_{ThisSide}$, this order (and subsequent ones $\ge Ask$) will execute immediately as Taker orders ("Sweep").
2.  **Subsequent Rungs:** Generate `LADDER_DEPTH - 1` additional orders.
3.  **Spacing:** Each rung is `0.01` lower than the previous.
4.  **Sizing:** All rungs use $Target\_Size$.

---

## 6. Execution Logic (Diff Engine)

Reconcile **Current Orders** (from exchange) with **Ideal Ladder**.

### Phase 1: Cancel Stale Orders
* **Logic:** Iterate through all `Current Orders`.
* **Check:** Is the order's price present in the `Ideal Ladder`?
* **Action:**
    * **No:** The order is off-ladder (market moved away). **CANCEL** immediately.
    * **Yes:** Keep it for Phase 2.

### Phase 2: Place or Update Rungs
* **Logic:** Iterate through every price level ($P$) in the `Ideal Ladder`.
* **Check:** Do we have an active order at $P$?
* **Action:**
    * **Case A (No Order):**
        * **PLACE** a new limit order at $P$ for `Target_Size`.
    * **Case B (Order Exists): Check Sizing**
        * **If `Current_Size` < `Target_Size`:**
            * **STACK:** Place a *new, separate* order at $P$ for (`Target_Size` - `Current_Size`).
        * **If `Current_Size` > `Target_Size` * (1 + HYSTERESIS):**
            * **SHRINK:** **CANCEL** the existing order and **PLACE** a new one at `Target_Size`.
        * **Otherwise (Within Hysteresis):**
            * **HOLD:** Do nothing.

---

## 7. Safety Protocols

1.  **Ghost Order Trap:** If Cancel API returns "Not Found," remove locally immediately. Do not retry.
2.  **Hard Stop:**
    * If $Net\_Pos \ge MAX\_POSITION$: Force $Target\_Size = 0$.
    * If $Net\_Pos \le -MAX\_POSITION$: Allow Aggressive Buying ($Scalar = 2.0$), but $P_{acct}$ will naturally cap price if losses are too deep.