# Polybot

A low-latency, event-driven trading bot for Polymarket's 15-minute cryptocurrency binary options. The bot implements a "Legging In" strategy that buys one side cheap and locks in guaranteed profit by buying the other side when prices align, effectively creating a "Box Spread" for less than $1.00.

## Architecture

The system uses a uni-directional data flow:

1. **Ingestion Layer (AsyncIO):** Connects to Polymarket (CLOB) and Binance (Oracle) WebSockets
2. **Atomic State Engine:** Reconstructs the Order Book and Global State in real-time
3. **Strategy Engine (Synchronous):** Pure function that takes `State` and returns `TradeSignals`
4. **Execution Layer (AsyncIO):** Manages order lifecycle, latency simulation, and position accounting

## Project Structure

```
polybot/
├── config.py              # Constants and configuration
├── ingestion/
│   ├── polymarket_ws.py   # Polymarket WebSocket handler
│   ├── binance_ws.py      # Binance WebSocket handler (TODO)
│   └── gamma_api.py       # Gamma API client for market discovery
├── state/
│   ├── market_state.py    # MarketState class (order book, timestamps)
│   └── position_state.py # PositionState class (inventory, cost basis)
├── strategy/
│   └── engine.py          # Strategy logic (TODO)
├── execution/
│   └── simulator.py       # Execution simulation (TODO)
├── main.py                # Entry point (TODO)
└── test_polymarket_ws.py  # Test script for WebSocket ingestion
```

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Test Polymarket WebSocket ingestion:
```bash
python test_polymarket_ws.py
```

This will:
- Fetch active 15-minute BTC markets from Gamma API
- Connect to Polymarket WebSocket
- Subscribe to market data
- Display real-time order book updates

## Current Status

✅ **Completed:**
- Project structure and configuration
- MarketState and PositionState classes
- Polymarket WebSocket ingestion (connection, message handling, book reconstruction)
- Gamma API client for market discovery
- Test script for WebSocket connection

🚧 **In Progress:**
- Binance WebSocket for BTC oracle data
- Strategy engine (oracle filter, synthetic arbitrage, inventory management)
- Execution simulator (latency, partial fills, position updates)
- Main event loop integrating all components

## Configuration

Key constants in `config.py`:
- `MAX_SHARES = 100`: Hard cap on exposure per side
- `BALANCE_PAD = 10`: Max allowable share mismatch
- `TARGET_PAIR = 980`: Target cost for complete pair (in ticks)
- `STOP_LOSS = 0.25`: Price threshold for panic-sell
- `FLOOR_THRESH = 0.20`: Do not average down below this price
- `LATENCY_MS = 0.150`: Simulated network latency (150ms)

## Strategy Overview

The bot implements a multi-stage strategy:

1. **Priority 0: Synthetic Arbitrage** - Buy both sides if `Best_Ask_YES + Best_Ask_NO < 1000`
2. **Priority 1: Oracle Filter** - Never trade against BTC price trend
3. **Priority 2: Inventory Management**:
   - **Bootstrap:** Leg into first position when price is cheap
   - **Hedging:** Complete the pair to lock profit
   - **Averaging Down:** Reduce average cost when market moves against us
4. **Priority 3: Bailout & Lock** - Stop loss and profit locking

## License

MIT
