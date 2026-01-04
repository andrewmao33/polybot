# Polybot

A low-latency, event-driven trading bot for Polymarket's 15-minute cryptocurrency binary options. The bot implements a "Legging In" strategy that buys one side cheap and locks in guaranteed profit by buying the other side when prices align, effectively creating a "Box Spread" for less than $1.00.

## Architecture

The system uses a uni-directional data flow:

1. **Ingestion Layer (AsyncIO):** Connects to Polymarket (CLOB) and Coinbase (Oracle) WebSockets
2. **State Engine:** Reconstructs the Order Book and Global State in real-time
3. **Strategy Engine (Synchronous):** Pure function that takes `State` and returns `TradeSignals`
4. **Execution Layer (AsyncIO):** Manages order lifecycle, latency simulation, and position accounting

## Project Structure

```
polybot/
├── config.py              # Constants and configuration
├── backtest.py            # Backtesting script for recorded data
├── ingestion/
│   ├── polymarket_ws.py   # Polymarket WebSocket handler
│   ├── coinbase_ws.py     # Coinbase WebSocket handler (BTC oracle)
│   ├── gamma_api.py       # Gamma API client for market discovery
│   └── orchestrator.py    # Ingestion orchestrator
├── state/
│   ├── market_state.py    # MarketState class (order book, timestamps)
│   └── position_state.py  # PositionState class (inventory, cost basis)
├── strategy/
│   ├── engine.py          # Main strategy evaluation
│   ├── stages.py          # Strategy stages (arbitrage, bootstrap, hedging, etc.)
│   ├── signals.py         # Trade signal definitions
│   └── oracle.py          # Oracle price model
├── execution/
│   ├── execution_engine.py # Main execution orchestrator
│   ├── simulator.py        # Simulated execution with latency
│   ├── backtest_executor.py # Backtest execution mode
│   ├── polymarket_api.py   # Polymarket API client
│   └── order_state.py      # Order state management
└── data/
    └── recorder.py         # Market data recording
```

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Test components:
```bash
python test_ingestion.py      # Test ingestion layer
python test_strategy.py        # Test strategy logic
python backtest.py             # Run backtest on recorded data
```

## Features

- **Real-time market data ingestion** from Polymarket WebSocket
- **Oracle integration** via Coinbase WebSocket for BTC price data
- **Multi-stage trading strategy** with priority-based decision making
- **Execution simulation** with configurable latency and partial fills
- **Backtesting** on recorded market data
- **Data recording** for analysis and backtesting

## Configuration

Key constants in `config.py`:
- `MAX_SHARES = 100`: Hard cap on exposure per side
- `BALANCE_PAD = 10`: Max allowable share mismatch
- `TARGET_PAIR = 980`: Target cost for complete pair (in ticks)
- `STOP_LOSS = 0.25`: Price threshold for panic-sell
- `FLOOR_THRESH = 0.20`: Do not average down below this price
- `LATENCY_MS = 150`: Simulated network latency (150ms)

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
