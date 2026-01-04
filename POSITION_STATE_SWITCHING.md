# Position State Handling During Market Switches

## Current Implementation

### How Position States are Changed

When markets switch in the orchestrator (every 15 minutes), here's what happens:

1. **New PositionState is Created** (line 163-164 in `orchestrator.py`):
   ```python
   from state.position_state import PositionState
   self.position_state = PositionState(market_id=metadata["market_id"])
   ```
   - Creates a **brand new** PositionState object with the new market_id
   - All position data (Qy, Qn, Cy, Cn) is reset to 0.0 (fresh start for new market)
   - Pending flags are also reset

2. **Old PositionState is Discarded**:
   - The previous PositionState object is no longer referenced by the orchestrator
   - Any positions from the previous market are lost/forgotten

### The Problem: Execution Engine Reference Stale

**Issue**: The execution engine maintains its own reference to the PositionState, but it's not updated when markets switch.

**Current Flow**:
1. At initialization (`test_strategy_live.py` line 187-188):
   ```python
   if orchestrator.position_state:
       execution_engine.set_position_state(orchestrator.position_state)
   ```
   - Execution engine gets a reference to the initial PositionState

2. When market switches (orchestrator line 164):
   ```python
   self.position_state = PositionState(market_id=metadata["market_id"])  # NEW object
   ```
   - Orchestrator creates a NEW PositionState
   - Execution engine still has reference to the OLD PositionState
   - **They are now out of sync!**

3. When fills happen:
   - Execution engine updates the OLD PositionState (wrong one)
   - Orchestrator's PositionState remains empty (new one, not updated)

### Code Locations

**Orchestrator creates new PositionState**:
- `_switch_markets_periodically()` line 163-164: Creates new PositionState on market switch
- `_initialize_market()` line 203: Creates new PositionState on initial market setup

**Execution Engine holds reference**:
- `execution/execution_engine.py` line 43: Stores `self.position_state`
- `execution/execution_engine.py` line 67-69: `set_position_state()` method to update reference
- `execution/execution_engine.py` line 115-119, 188-202: Updates position state on fills

**Initial Connection**:
- `test_strategy_live.py` line 187-188: Sets execution engine's position_state reference (only once)

## What Should Happen

When markets switch, the execution engine should also get the new PositionState reference so that:
1. Fills update the correct (current) PositionState
2. Strategy evaluation sees the correct position data
3. Position state stays in sync between orchestrator and execution engine

## Potential Solutions

### Option 1: Update Execution Engine Reference (Recommended)
Have the orchestrator notify or provide a way for the execution engine to get the new PositionState when markets switch.

**Challenge**: Orchestrator doesn't know about execution engine (good separation of concerns).

**Possible approaches**:
- Add callback mechanism: `on_position_state_reset` callback
- Have execution engine poll orchestrator's position_state
- Pass execution engine reference to orchestrator (creates coupling)

### Option 2: Single Source of Truth
Make orchestrator the single source of truth, and execution engine always queries orchestrator for position state rather than holding a reference.

### Option 3: Position State Manager
Create a separate position state manager that both orchestrator and execution engine reference.

## Current Workaround

Currently, if you run the system for multiple markets:
- Positions from the old market are lost
- New market starts with empty positions (Qy=0, Qn=0)
- This might be intentional if you want to reset positions for each 15-minute market
- But execution engine updates go to the wrong object, so there's a bug

