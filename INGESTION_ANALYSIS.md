# Ingestion System Analysis

## Overview
The ingestion system coordinates real-time data from two sources:
1. **Polymarket WebSocket**: Order book data (bids/asks for YES/NO tokens)
2. **Coinbase WebSocket**: BTC price oracle data

## Architecture

### Components

1. **IngestionOrchestrator** (`ingestion/orchestrator.py`)
   - Manages both WebSocket connections
   - Handles market discovery and switching at 15-minute intervals
   - Coordinates state updates via callbacks
   - Maintains MarketState and PositionState

2. **PolymarketWebSocket** (`ingestion/polymarket_ws.py`)
   - Connects to Polymarket CLOB WebSocket
   - Subscribes to order book data for YES/NO tokens
   - Handles book snapshots and price change deltas
   - Updates `market_state.order_book_yes_bids/asks` and `order_book_no_bids/asks`
   - **Calls `on_state_update` callback** when order book changes

3. **CoinbaseWebSocket** (`ingestion/coinbase_ws.py`)
   - Connects to Coinbase WebSocket
   - Subscribes to BTC-USD ticker
   - Updates `market_state.btc_price`
   - **DOES NOT call state update callback** (ISSUE #1)

4. **MarketState** (`state/market_state.py`)
   - Atomic state container for order books, BTC price, timestamps
   - `snapshot()` method creates immutable copy for strategy evaluation
   - **Missing `slug` in snapshot** (ISSUE #2)

## Data Flow

1. **Initialization**:
   - Orchestrator fetches current market from Gamma API
   - Creates MarketState with market metadata
   - Creates PolymarketWebSocket with `on_state_update` callback
   - Creates CoinbaseWebSocket (without callback - ISSUE)

2. **Runtime**:
   - Polymarket WS receives order book updates → updates MarketState → calls callback
   - Coinbase WS receives BTC price → updates MarketState → **no callback** (ISSUE)
   - Callback triggers strategy evaluation

3. **Market Switching** (every 15 minutes):
   - Orchestrator detects market end time
   - Fetches new market metadata
   - Updates MarketState fields
   - Polymarket WS switches subscriptions
   - Order books reset, sync status cleared

## Issues Identified and Fixed

### Issue #1: Coinbase WebSocket Doesn't Trigger State Updates ✅ FIXED
**Problem**: When BTC price updates, Coinbase WS updates `market_state.btc_price` but doesn't call the orchestrator's callback. This means:
- Strategies won't be notified of BTC price changes
- Strategy evaluation only happens on order book updates
- BTC price changes won't trigger trading signals

**Fix Applied**: 
- Updated `CoinbaseWebSocket.__init__()` to accept `on_state_update` callback (same as PolymarketWebSocket)
- Updated `_process_message()` to call `on_state_update(market_state.snapshot())` when BTC price updates
- Updated `IngestionOrchestrator._initialize_market()` to pass `_on_market_state_update` callback to CoinbaseWebSocket

### Issue #2: Missing `slug` in MarketState.snapshot() ✅ FIXED
**Problem**: The `snapshot()` method doesn't copy the `slug` property, but it's used in strategy code (`test_strategy_live.py` line 110, 125).

**Fix Applied**: Added `snapshot.slug = self.slug` to the `snapshot()` method.

## Verification

To verify ingestion is working properly:

1. **Check MarketState Updates**:
   - Order books should populate (YES and NO bids/asks)
   - BTC price should update regularly
   - Sync status should become True when both books sync
   - Timestamps should update

2. **Check Callbacks**:
   - Callback should fire on order book updates (from Polymarket)
   - Callback should fire on BTC price updates (from Coinbase - after fix)
   - Strategy evaluation should happen on each callback

3. **Check Market Switching**:
   - Markets should switch every 15 minutes
   - Order books should reset on switch
   - Sync status should reset on switch

## How MarketState Gets Updated

### From Polymarket WS:
- `order_book_yes_bids/asks` - updated on book snapshots and price changes
- `order_book_no_bids/asks` - updated on book snapshots and price changes
- `exchange_timestamp` - updated from message timestamps
- `sync_status_yes/no` - set to True when book snapshot received
- Callback triggered after each update

### From Coinbase WS:
- `btc_price` - updated on ticker messages
- Callback **not** triggered (ISSUE - needs fix)

