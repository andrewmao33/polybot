# Option 1: Callback Mechanism for Position State Updates

## Overview

Add a callback mechanism to the orchestrator that gets invoked whenever the position state is reset (new market starts). The callback receives the new PositionState, allowing the execution engine (or any other component) to update its reference.

## How It Works

### 1. Add Callback Parameter to Orchestrator

Similar to how `on_market_state_update` works, we add an `on_position_state_reset` callback:

```python
class IngestionOrchestrator:
    def __init__(
        self,
        on_market_state_update: Optional[Callable[[MarketState], None]] = None,
        on_position_state_reset: Optional[Callable[[PositionState], None]] = None  # NEW
    ):
        self.on_market_state_update = on_market_state_update
        self.on_position_state_reset = on_position_state_reset  # NEW
        # ... rest of init
```

### 2. Call the Callback When Position State Resets

In both places where a new PositionState is created, invoke the callback:

**In `_switch_markets_periodically()` (line ~164):**
```python
# Reset position state for new market
from state.position_state import PositionState
self.position_state = PositionState(market_id=metadata["market_id"])

# Notify callback about new position state
if self.on_position_state_reset:
    self.on_position_state_reset(self.position_state)

self.current_slug = new_slug
```

**In `_initialize_market()` (line ~203):**
```python
# Reset position state for new market
self.position_state = PositionState(market_id=metadata["market_id"])

# Notify callback about new position state
if self.on_position_state_reset:
    self.on_position_state_reset(self.position_state)

self.current_slug = metadata.get("slug", "")
```

### 3. Update test_strategy_live.py to Use the Callback

When creating the orchestrator, provide a callback that updates the execution engine:

```python
# Create execution engine
execution_engine = ExecutionEngine(
    mode=config.EXECUTION_MODE,
    position_state=None  # Will be set via callback
)

# Create callback for position state resets
def position_state_reset_callback(new_position_state: PositionState):
    """Called whenever orchestrator creates a new PositionState."""
    execution_engine.set_position_state(new_position_state)
    logger.info(f"✅ Execution engine position state updated for market: {new_position_state.market_id}")

# Create orchestrator with both callbacks
orchestrator = IngestionOrchestrator(
    on_market_state_update=market_update_callback,
    on_position_state_reset=position_state_reset_callback  # NEW
)

# Still set initial position state (callback will also fire, but this ensures it's set)
await orchestrator.initialize()
if orchestrator.position_state:
    execution_engine.set_position_state(orchestrator.position_state)
```

## Benefits

1. **Decoupled Design**: Orchestrator doesn't need to know about execution engine
2. **Flexible**: Any component can register to be notified of position state changes
3. **Automatic Sync**: Execution engine reference updates automatically on market switches
4. **Consistent Pattern**: Follows the same pattern as `on_market_state_update`

## Flow Diagram

```
Market Switch Event
    ↓
Orchestrator creates new PositionState
    ↓
on_position_state_reset(new_position_state) callback invoked
    ↓
Execution Engine.set_position_state(new_position_state)
    ↓
Execution Engine now references correct PositionState
    ↓
Future fills update the correct PositionState
```

## Implementation Details

### Callback Signature

```python
Callable[[PositionState], None]
```

- Takes the new PositionState as argument
- Returns None
- Can be sync or async (if async, orchestrator would need to handle it)

### When Callback is Invoked

1. **Initial Market Setup**: When `_initialize_market()` is called during `initialize()`
2. **Market Switches**: When `_switch_markets_periodically()` detects a market switch

### Error Handling

The callback should handle errors gracefully:
- If callback raises exception, log it but don't crash orchestrator
- Execution engine's `set_position_state()` is simple assignment, unlikely to fail

### Thread Safety

- Position state creation happens in orchestrator's async context
- Callback invocation is synchronous (simple function call)
- Execution engine's `set_position_state()` is just assignment, thread-safe for Python's GIL

## Alternative: Query-Based Approach

Instead of callbacks, execution engine could query orchestrator:

```python
# In strategy evaluation callback:
position = orchestrator.get_position_state()  # Always get current reference
signals = evaluate_strategy(market_snapshot, position)
```

**Pros**: Simpler, no callback needed
**Cons**: Must remember to query every time (easy to forget), slight performance overhead

## Recommended Implementation

Use the callback approach (Option 1) because:
- It's explicit and clear when position state changes
- Execution engine automatically stays in sync
- Follows existing pattern in codebase
- Prevents bugs from stale references

