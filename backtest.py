"""
Simple backtesting script.
Processes recorded market data and calculates P&L.
"""
import json
import sys
import logging
from pathlib import Path
from state.market_state import MarketState
from state.position_state import PositionState
from strategy.engine import evaluate_strategy
from execution.execution_engine import ExecutionEngine

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def load_recorded_data(data_dir: str):
    """Load market data from recorded data directory."""
    data_path = Path(data_dir)
    market_data_file = data_path / "market_data.jsonl"
    metadata_file = data_path / "metadata.json"
    
    if not market_data_file.exists():
        raise FileNotFoundError(f"Market data file not found: {market_data_file}")
    
    # Load metadata
    metadata = {}
    if metadata_file.exists():
        with open(metadata_file) as f:
            metadata = json.load(f)
    
    # Load market data
    records = []
    with open(market_data_file) as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    
    logger.info(f"Loaded {len(records)} records from {data_dir}")
    return records, metadata


def reconstruct_market_state(record: dict) -> MarketState:
    """Reconstruct MarketState from a recorded data point."""
    market_state = MarketState(
        market_id=record["market_id"],
        strike_price=record.get("strike_price", 0.0),
        end_timestamp=record.get("end_timestamp", 0)
    )
    
    # Reconstruct order books (only best bid/ask since that's what's recorded)
    if record.get("yes_bid") is not None:
        market_state.order_book_yes_bids[record["yes_bid"]] = record.get("yes_bid_size", 0.0)
    if record.get("yes_ask") is not None:
        market_state.order_book_yes_asks[record["yes_ask"]] = record.get("yes_ask_size", 0.0)
    if record.get("no_bid") is not None:
        market_state.order_book_no_bids[record["no_bid"]] = record.get("no_bid_size", 0.0)
    if record.get("no_ask") is not None:
        market_state.order_book_no_asks[record["no_ask"]] = record.get("no_ask_size", 0.0)
    
    market_state.btc_price = record.get("btc_price")
    market_state.exchange_timestamp = record.get("timestamp_ms")
    market_state.sync_status_yes = record.get("sync_status", False)
    market_state.sync_status_no = record.get("sync_status", False)
    
    return market_state


def calculate_pnl(position: PositionState, strike_price: float, btc_price: float) -> float:
    """Calculate P&L for a resolved market."""
    if btc_price is None or strike_price <= 0:
        return 0.0
    
    # Determine winner: BTC > strike = YES wins, BTC < strike = NO wins
    yes_wins = btc_price > strike_price
    
    pnl = 0.0
    
    if yes_wins:
        # YES shares pay out 1000 ticks each
        if position.Qy > 0:
            pnl += (position.Qy * 1000.0) - position.Cy
        # NO shares are worthless
        pnl -= position.Cn
    else:
        # NO shares pay out 1000 ticks each
        if position.Qn > 0:
            pnl += (position.Qn * 1000.0) - position.Cn
        # YES shares are worthless
        pnl -= position.Cy
    
    return pnl


def main():
    if len(sys.argv) < 2:
        print("Usage: python backtest.py <recorded_data_folder>")
        sys.exit(1)
    
    data_dir = sys.argv[1]
    
    # Load data
    records, metadata = load_recorded_data(data_dir)
    if not records:
        logger.error("No records found")
        return
    
    # Sort records by timestamp
    records.sort(key=lambda r: r.get("timestamp_ms", 0))
    
    # Track markets and their last state
    market_positions: dict[str, PositionState] = {}
    market_last_state: dict[str, MarketState] = {}
    market_end_timestamps: dict[str, int] = {}
    completed_markets = set()
    
    # Track all trades
    all_trades = []
    
    # Track consumed liquidity per market (to simulate market impact)
    # Format: {market_id: {side: {price: consumed_size}}}
    consumed_liquidity = {}
    
    # Execution engine
    execution_engine = ExecutionEngine(mode="backtest")
    
    # Process records chronologically
    current_market_id = None
    position = None
    
    for record in records:
        market_id = record["market_id"]
        
        # Market switch detected
        if market_id != current_market_id:
            current_market_id = market_id
            
            # Create new position for this market
            position = PositionState(market_id=market_id)
            market_positions[market_id] = position
            execution_engine.set_position_state(position)
            
            # Reset consumed liquidity for new market
            consumed_liquidity[market_id] = {"YES": {}, "NO": {}}
            
            # Store end timestamp
            if record.get("end_timestamp"):
                market_end_timestamps[market_id] = record["end_timestamp"]
        
        # Reconstruct market state
        market_state = reconstruct_market_state(record)
        market_last_state[market_id] = market_state
        
        # Apply consumed liquidity to market state (simulate market impact)
        if market_id in consumed_liquidity:
            for side, price_map in consumed_liquidity[market_id].items():
                if side == "YES":
                    for price, consumed in price_map.items():
                        if price in market_state.order_book_yes_asks:
                            current_size = market_state.order_book_yes_asks[price]
                            new_size = max(0, current_size - consumed)
                            if new_size > 0:
                                market_state.order_book_yes_asks[price] = new_size
                            else:
                                market_state.order_book_yes_asks.pop(price, None)
                else:  # NO
                    for price, consumed in price_map.items():
                        if price in market_state.order_book_no_asks:
                            current_size = market_state.order_book_no_asks[price]
                            new_size = max(0, current_size - consumed)
                            if new_size > 0:
                                market_state.order_book_no_asks[price] = new_size
                            else:
                                market_state.order_book_no_asks.pop(price, None)
        
        # Check if market completed
        if market_id in market_end_timestamps:
            end_ts = market_end_timestamps[market_id]
            if record.get("timestamp_ms", 0) >= end_ts:
                completed_markets.add(market_id)
        
        # Only evaluate if books are synced
        if not market_state.sync_status:
            continue
        
        # Evaluate strategy
        signals = evaluate_strategy(market_state, position)
        
        # Execute signals
        if signals:
            for signal in signals:
                try:
                    # For backtest mode, execute synchronously
                    if execution_engine.mode == "backtest":
                        # Create order
                        import uuid
                        order_id = f"order_{execution_engine.order_counter}_{uuid.uuid4().hex[:8]}"
                        execution_engine.order_counter += 1
                        
                        from execution.order_state import OrderState, OrderStatus
                        order = OrderState(
                            order_id=order_id,
                            side=signal.side,
                            price=signal.price,
                            size=signal.size,
                            status=OrderStatus.PENDING
                        )
                        execution_engine.orders[order_id] = order
                        
                        # Set pending flags
                        if signal.side == "YES":
                            position.pending_yes = True
                        else:
                            position.pending_no = True
                        
                        # Submit order (synchronous for backtest)
                        execution_engine.executor.submit_order(signal, market_state, order_id, order)
                        
                        # Track trade if order was filled (check fills list)
                        if order.fills:
                            for fill in order.fills:
                                all_trades.append({
                                    "timestamp_ms": record.get("timestamp_ms", 0),
                                    "market_id": market_id,
                                    "side": signal.side,
                                    "price": fill.price,
                                    "size": fill.size,
                                    "order_id": order_id,
                                    "reason": signal.reason,
                                    "priority": signal.priority
                                })
                                
                                # Track consumed liquidity
                                if market_id not in consumed_liquidity:
                                    consumed_liquidity[market_id] = {"YES": {}, "NO": {}}
                                if fill.price not in consumed_liquidity[market_id][signal.side]:
                                    consumed_liquidity[market_id][signal.side][fill.price] = 0.0
                                consumed_liquidity[market_id][signal.side][fill.price] += fill.size
                    else:
                        import asyncio
                        asyncio.run(execution_engine.execute_signal(signal, market_state))
                except Exception as e:
                    logger.error(f"Error executing signal: {e}")
    
    # Calculate P&L for completed markets (exclude last incomplete market)
    all_markets = list(market_positions.keys())
    if all_markets:
        # Last market is incomplete, exclude it
        completed_markets.discard(all_markets[-1])
    
    total_pnl = 0.0
    total_cost = 0.0
    
    logger.info("\n" + "="*60)
    logger.info("BACKTEST RESULTS")
    logger.info("="*60)
    
    # Print all trades
    if all_trades:
        logger.info(f"\nTRADES ({len(all_trades)} total):")
        logger.info("-" * 60)
        for i, trade in enumerate(all_trades, 1):
            timestamp = trade["timestamp_ms"]
            time_str = f"{timestamp/1000:.1f}s" if timestamp else "N/A"
            logger.info(f"{i:4d}. [{time_str}] {trade['side']:3s} @ ${trade['price']/1000:.3f} ({trade['price']:.1f} ticks) × {trade['size']:.2f} shares")
            logger.info(f"      Market: {trade['market_id'][:16]}... | Priority: {trade['priority']} | {trade['reason']}")
        logger.info("-" * 60)
    else:
        logger.info("\nNo trades executed.")
    
    for market_id in sorted(completed_markets):
        position = market_positions[market_id]
        last_state = market_last_state.get(market_id)
        
        if not last_state or last_state.strike_price <= 0 or last_state.btc_price is None:
            continue
        
        pnl = calculate_pnl(position, last_state.strike_price, last_state.btc_price)
        cost = position.Cy + position.Cn
        
        total_pnl += pnl
        total_cost += cost
        
        # Count trades for this market
        market_trades = [t for t in all_trades if t["market_id"] == market_id]
        
        logger.info(f"\nMarket: {market_id[:16]}...")
        logger.info(f"  Trades: {len(market_trades)}")
        logger.info(f"  YES: {position.Qy:.2f} shares @ ${position.Cy/1000:.2f}")
        logger.info(f"  NO:  {position.Qn:.2f} shares @ ${position.Cn/1000:.2f}")
        logger.info(f"  Strike: ${last_state.strike_price:,.0f}")
        logger.info(f"  BTC at resolution: ${last_state.btc_price:,.0f}")
        logger.info(f"  Winner: {'YES' if last_state.btc_price > last_state.strike_price else 'NO'}")
        logger.info(f"  Cost: ${cost/1000:.2f}")
        logger.info(f"  P&L: ${pnl/1000:.2f}")
    
    logger.info("\n" + "="*60)
    logger.info(f"Total Cost: ${total_cost/1000:.2f}")
    logger.info(f"Total P&L: ${total_pnl/1000:.2f}")
    logger.info(f"ROI: {(total_pnl/total_cost*100) if total_cost > 0 else 0:.2f}%")
    logger.info("="*60)


if __name__ == "__main__":
    main()

