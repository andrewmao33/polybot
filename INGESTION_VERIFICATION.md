# How to Verify Ingestion is Working Properly

## Quick Verification

Run the test ingestion script to see real-time updates:

```bash
python test_ingestion.py
```

You should see:
1. ✅ Connection messages from both WebSockets
2. 📊 Market state updates with order book data
3. BTC price updates
4. Sync status becoming True when both books sync

## What to Look For

### 1. Market State Updates

The callback should fire and log updates showing:

```
📊 MARKET STATE UPDATE
Market ID: 0x...
Strike Price: $100,000
BTC Price: $97,234.56
Exchange Timestamp: 1738456789000
Sync Status: YES=True, NO=True
Time Remaining: 12.34 minutes

✅ YES - Bid: 485.2 (10.5) | Ask: 487.3 (8.2)
✅ NO  - Bid: 512.1 (9.8) | Ask: 514.5 (7.3)
💰 Synthetic Spread: 1001.8 ticks (arbitrage if < 1000)
```

### 2. Order Book Data

- **YES bids/asks**: Should populate with price levels (in ticks: 0-1000)
- **NO bids/asks**: Should populate with price levels (in ticks: 0-1000)
- **Best bid < Best ask**: For both YES and NO tokens
- **Synthetic spread**: YES ask + NO ask should be close to 1000 ticks

### 3. BTC Price Updates

- BTC price should update regularly (every few seconds)
- Price should be realistic (currently ~$95k-$100k)
- **After fix**: Callback should fire on BTC price updates (not just order book updates)

### 4. Sync Status

- Initially: `Sync Status: YES=False, NO=False`
- After first book snapshot: One side becomes True
- After both snapshots: `Sync Status: YES=True, NO=True`
- Strategy evaluation only happens when both are True

### 5. Market Switching (every 15 minutes)

- Log message: `⏰ Waiting Xs until next market switch`
- At switch time: `🔄 Switching market: old-slug -> new-slug`
- Order books should reset
- Sync status should reset to False
- New market data should populate

## How MarketState Gets Updated

### From Polymarket WebSocket:

1. **Book Snapshots** (on subscription and market switch):
   - Clears existing order book for that side
   - Populates with new snapshot data
   - Sets `sync_status_yes` or `sync_status_no` to True
   - Updates `exchange_timestamp`
   - **Calls callback**: `on_state_update(market_state.snapshot())`

2. **Price Changes** (deltas):
   - Updates specific price levels (adds/removes orders)
   - Updates `exchange_timestamp`
   - **Calls callback**: `on_state_update(market_state.snapshot())`

### From Coinbase WebSocket (✅ FIXED):

1. **Ticker Messages**:
   - Updates `market_state.btc_price`
   - **NOW CALLS CALLBACK**: `on_state_update(market_state.snapshot())` ✅
   - Strategy can now react to BTC price changes

## Testing the Fixes

### Test Coinbase Callback Fix:

1. Run `test_ingestion.py`
2. Watch for BTC price updates in the logs
3. **Verify**: Callback fires on BTC price updates (you'll see "📊 MARKET STATE UPDATE" messages triggered by BTC price changes, not just order book changes)

### Test Snapshot Slug Fix:

1. Run `test_strategy_live.py`
2. Check logs for market slug in update messages
3. **Verify**: No AttributeError on `state.slug` (should show slug like "btc-updown-15m-1738456789")

## Expected Behavior

### Normal Operation:

1. **Startup**:
   - Fetches current market from Gamma API
   - Connects to Polymarket WS (subscribes to order books)
   - Connects to Coinbase WS (subscribes to BTC-USD ticker)
   - Order books start populating
   - BTC price starts updating

2. **Runtime**:
   - Order book updates trigger strategy evaluation
   - BTC price updates **now also trigger strategy evaluation** ✅
   - Market state updates flow through callback → strategy engine

3. **Market Switch** (every 15 min):
   - Detects market end time
   - Fetches new market
   - Updates MarketState fields
   - Polymarket WS switches subscriptions
   - Order books reset and repopulate

## Troubleshooting

### Order books not populating:
- Check Polymarket WS connection status
- Verify subscription message was sent
- Check for error messages in logs

### BTC price not updating:
- Check Coinbase WS connection status
- Verify subscription to BTC-USD ticker
- Check for error messages in logs

### Callback not firing:
- **Before fix**: BTC price updates wouldn't trigger callback
- **After fix**: Both order book AND BTC price updates trigger callback ✅

### Sync status stuck at False:
- Wait for book snapshots (usually arrives within seconds)
- Check if WebSocket is receiving messages
- Verify asset IDs match

