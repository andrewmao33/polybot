# Execution Layer Walkthrough

## Overview
This document walks through the complete execution flow from when `evaluate_strategy` is called after a market state update, through order submission, fill processing, and position updates.

---

## 1. Market State Update Trigger

### 1.1 WebSocket Message Arrives
- **Location**: `ingestion/polymarket_ws.py` or `ingestion/coinbase_ws.py`
- **Event**: WebSocket receives a message (book snapshot or price change)
- **Action**: Order book is updated in `MarketState` object

### 1.2 Callback Invocation
- **Location**: `ingestion/polymarket_ws.py:223` or `ingestion/polymarket_ws.py:293`
- **Code**:
  ```python
  if self.on_state_update:
      self.on_state_update(self.market_state.snapshot())
  ```
- **What happens**: Creates a snapshot of market state (atomic copy) and calls the callback

### 1.3 Orchestrator Callback
- **Location**: `ingestion/orchestrator.py:94-97`
- **Function**: `_on_market_state_update(state: MarketState)`
- **Action**: Forwards the callback to the registered handler:
  ```python
  def _on_market_state_update(self, state: MarketState):
      if self.on_market_state_update:
          self.on_market_state_update(state)
  ```

### 1.4 Strategy Evaluation Callback
- **Location**: `test_strategy_live.py:37-139`
- **Function**: `on_market_state_update(state: MarketState, execution_engine: ExecutionEngine)`
- **Key checks**:
  - Verifies both order books are synced (`state.sync_status`)
  - Creates atomic snapshot: `market_snapshot = state.snapshot()`
  - Gets position state: `position = execution_engine.position_state`

---

## 2. Strategy Evaluation

### 2.1 Strategy Engine Call
- **Location**: `test_strategy_live.py:111`
- **Code**:
  ```python
  signals = evaluate_strategy(market_snapshot, position)
  ```
- **Input**: Atomic market snapshot + current position state
- **Output**: `Optional[List[TradeSignal]]` - list of buy signals or None

### 2.2 Strategy Logic
- **Location**: `strategy/engine.py:20-96`
- **Priority Order**:
  1. **Priority 3 (Safety)**: Profit lock check → returns `None` if locked
  2. **Priority 3 (Safety)**: Stop loss check → returns `None` if triggered
  3. **Priority 0**: Synthetic arbitrage → returns signals (bypasses oracle)
  4. **Priority 2**: Inventory management (bootstrap, hedging, averaging down)
  5. **Priority 1**: Oracle filter applied to inventory signals
- **Returns**: List of `TradeSignal` objects or `None`

### 2.3 TradeSignal Structure
- **Location**: `strategy/signals.py`
- **Fields**:
  - `side`: "YES" or "NO"
  - `price`: Limit price in ticks (0-1000)
  - `size`: Order size in shares
  - `priority`: Signal priority level
  - `reason`: Human-readable reason

---

## 3. Order Execution

### 3.1 Signal Processing Loop
- **Location**: `test_strategy_live.py:129-134`
- **Code**:
  ```python
  for signal in signals:
      try:
          order = await execution_engine.execute_signal(signal, state)
          logger.info(f"✅ ORDER SUBMITTED: {order.order_id}")
      except Exception as e:
          logger.error(f"❌ ORDER FAILED: {e}")
  ```
- **Action**: Iterates through each signal and submits to execution engine

### 3.2 Execution Engine Entry Point
- **Location**: `execution/execution_engine.py:71-133`
- **Function**: `execute_signal(signal: TradeSignal, market_state: Optional[MarketState] = None)`
- **Steps**:

#### Step 3.2.1: Order ID Generation
```python
order_id = f"order_{self.order_counter}_{uuid.uuid4().hex[:8]}"
self.order_counter += 1
```

#### Step 3.2.2: Create OrderState
```python
order = OrderState(
    order_id=order_id,
    side=signal.side,
    price=signal.price,
    size=signal.size,
    status=OrderStatus.PENDING
)
self.orders[order_id] = order  # Track order immediately
```

#### Step 3.2.3: Set Pending Flags
```python
if signal.side == "YES":
    self.position_state.pending_yes = True
else:
    self.position_state.pending_no = True
```
- **Purpose**: Prevents duplicate orders on the same side

#### Step 3.2.4: Route to Executor
- **Simulated Mode**: `await self.executor.submit_order(signal, market, order_id, order)`
- **Real Mode**: `await self._submit_real_order(signal, order_id)`

#### Step 3.2.5: Notify Callback
```python
if self.on_order_update:
    self.on_order_update(order)
```

---

## 4. Simulated Execution (Default Mode)

### 4.1 Simulator Entry
- **Location**: `execution/simulator.py:40-65`
- **Function**: `submit_order(signal, market_state, order_id, order)`

### 4.2 Network Latency Simulation
- **Location**: `execution/simulator.py:60`
- **Code**: `await asyncio.sleep(config.LATENCY_MS / 1000.0)`
- **Purpose**: Simulates network round-trip time

### 4.3 Fill Scheduling
- **Location**: `execution/simulator.py:67-115`
- **Function**: `_schedule_fills(order, market_state)`
- **Logic**:
  1. Check available liquidity at order price
  2. If `available_size >= order.size`: Full fill after latency
  3. If `available_size < order.size`: Partial fills (30% then remaining)

### 4.4 Fill Execution
- **Location**: `execution/simulator.py:136-171`
- **Function**: `_execute_fill(order_id, fill_size, fill_price)`
- **Steps**:
  1. Get order from `pending_orders` dict
  2. Calculate actual fill size (don't overfill)
  3. Call `order.add_fill(actual_fill_size, fill_price)`
  4. Trigger callback: `self.on_fill(order_id, actual_fill_size, fill_price)`

---

## 5. Fill Processing

### 5.1 Fill Callback
- **Location**: `execution/execution_engine.py:169-206`
- **Function**: `_on_fill(order_id: str, filled_size: float, fill_price: float)`
- **Triggered by**: Simulator calls `on_fill` callback after each fill

### 5.2 Order State Update
- **Note**: Order state was already updated in simulator via `order.add_fill()`
- **Location**: `execution/order_state.py:53-85`
- **What happens**:
  - Adds `Fill` object to `order.fills` list
  - Updates `order.filled_size`
  - Calculates `order.avg_fill_price`
  - Updates `order.status` (PENDING → PARTIALLY_FILLED → FILLED)

### 5.3 Position State Update
- **Location**: `execution/execution_engine.py:188-202`
- **Code**:
  ```python
  if order.side == "YES":
      self.position_state.Qy += filled_size
      self.position_state.Cy += fill_price * filled_size
      if order.status == OrderStatus.FILLED:
          self.position_state.pending_yes = False
  else:  # NO
      self.position_state.Qn += filled_size
      self.position_state.Cn += fill_price * filled_size
      if order.status == OrderStatus.FILLED:
          self.position_state.pending_no = False
  ```
- **Updates**:
  - `Qy` / `Qn`: Quantity of YES/NO shares
  - `Cy` / `Cn`: Total cost basis for YES/NO
  - `pending_yes` / `pending_no`: Cleared when order fully filled

### 5.4 Order Update Notification
- **Location**: `execution/execution_engine.py:205-206`
- **Code**:
  ```python
  if self.on_order_update:
      self.on_order_update(order)
  ```
- **Purpose**: Notifies external systems (logging, UI, etc.) of order state changes

---

## 6. Real Execution Mode (Alternative)

### 6.1 API Order Submission
- **Location**: `execution/execution_engine.py:135-167`
- **Function**: `_submit_real_order(signal, order_id)`
- **Steps**:
  1. Get asset ID from market state
  2. Convert price from ticks to decimal: `price_decimal = signal.price / 1000.0`
  3. Submit via API: `api_order_id = await self.api_client.submit_order(...)`
  4. Store API order ID in `order.api_order_id` for status tracking

### 6.2 Real Order Tracking
- **Note**: Real orders require polling API for fill status
- **Location**: `execution/polymarket_api.py` (not shown in detail)
- **Difference**: Fills come from API responses, not simulated market depth

---

## 7. Order Lifecycle States

### 7.1 OrderStatus Enum
- **Location**: `execution/order_state.py:10-16`
- **States**:
  - `PENDING`: Order submitted, not yet filled
  - `PARTIALLY_FILLED`: Some fills received, more pending
  - `FILLED`: Order completely filled
  - `CANCELLED`: Order cancelled
  - `REJECTED`: Order rejected by exchange

### 7.2 Order Tracking
- **Location**: `execution/execution_engine.py:57`
- **Storage**: `self.orders: Dict[str, OrderState]`
- **Access**: `get_order(order_id)` or `get_pending_orders()`

---

## 8. Complete Flow Diagram

```
WebSocket Message
    ↓
MarketState.update_order_book()
    ↓
on_state_update(market_state.snapshot())
    ↓
orchestrator._on_market_state_update()
    ↓
on_market_state_update() [test_strategy_live.py]
    ↓
evaluate_strategy(market_snapshot, position)
    ↓
[Returns List[TradeSignal] or None]
    ↓
for signal in signals:
    ↓
execution_engine.execute_signal(signal, state)
    ↓
[Create OrderState, set pending flags]
    ↓
simulator.submit_order() [or real API]
    ↓
[Simulate latency + fills]
    ↓
_execute_fill() → order.add_fill()
    ↓
on_fill(order_id, size, price)
    ↓
execution_engine._on_fill()
    ↓
[Update position_state: Qy/Qn, Cy/Cn]
    ↓
[Clear pending flags if filled]
    ↓
on_order_update(order) [notify external systems]
```

---

## 9. Key Design Patterns

### 9.1 Atomic Snapshots
- **Why**: Market state changes during strategy evaluation
- **How**: `market_state.snapshot()` creates immutable copy
- **Location**: `state/market_state.py:126-145`

### 9.2 Pending Flags
- **Why**: Prevent duplicate orders on same side
- **How**: `position_state.pending_yes` / `pending_no`
- **Cleared**: When order status becomes `FILLED`

### 9.3 Order Tracking
- **Why**: Fills arrive asynchronously, need to find order
- **How**: `self.orders[order_id]` dict tracks all orders
- **Created**: Before execution starts (so fills can find it)

### 9.4 Callback Chain
- **Pattern**: WebSocket → Orchestrator → Strategy → Execution
- **Purpose**: Loose coupling, testability
- **Flow**: Each layer can be swapped independently

---

## 10. Important Notes

1. **Order State is Updated in Simulator**: The `order.add_fill()` call happens in the simulator, not the execution engine. The execution engine's `_on_fill()` callback only updates position state.

2. **Position State is Shared**: The same `PositionState` object is used by both the strategy engine (for evaluation) and execution engine (for updates). This ensures consistency.

3. **Market State is Snapshot**: Strategy always receives a snapshot, so it sees atomic state even if market updates during evaluation.

4. **Pending Flags Prevent Duplicates**: If `pending_yes = True`, the strategy should not generate another YES signal (though this is enforced at strategy level, not execution level).

5. **Fills are Asynchronous**: In simulated mode, fills happen after latency delays. Multiple fills can occur for a single order (partial fills).

6. **Order Tracking is Critical**: Orders must be added to `self.orders` dict BEFORE execution starts, so that fill callbacks can find them.

