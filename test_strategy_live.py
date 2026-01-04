"""
Live strategy testing with real market data.
Integrates strategy engine with ingestion layer and execution layer.
"""
import asyncio
import logging
import time
import sys
from ingestion.orchestrator import IngestionOrchestrator
from state.market_state import MarketState
from state.position_state import PositionState
from strategy.engine import evaluate_strategy
from execution.execution_engine import ExecutionEngine
import config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Reduce noise from WebSocket message logging (but keep connection/sync messages)
logging.getLogger('ingestion.polymarket_ws').setLevel(logging.INFO)  # Show connection/sync messages
logging.getLogger('ingestion.coinbase_ws').setLevel(logging.INFO)   # Show connection messages

# Suppress the verbose message logging
import logging.handlers
ws_logger = logging.getLogger('ingestion.polymarket_ws')
# Create a filter to suppress the "Message #X" logs
class MessageFilter(logging.Filter):
    def filter(self, record):
        return "Message #" not in record.getMessage()
ws_logger.addFilter(MessageFilter())


async def on_market_state_update(state: MarketState, execution_engine: ExecutionEngine):
    """Callback when market state updates - evaluate strategy."""
    # Only evaluate if both order books are synced
    if not state.sync_status:
        return  # Wait for both books to sync
    
    # Log when we first start evaluating (after sync)
    if not hasattr(on_market_state_update, '_first_eval'):
        on_market_state_update._first_eval = True
        on_market_state_update._start_time = time.time()
        on_market_state_update._last_portfolio_log = time.time()
        logger.info("\n" + "="*60)
        logger.info("✅ ORDER BOOKS SYNCED - Strategy evaluation started")
        logger.info("="*60 + "\n")
    on_market_state_update._first_eval = False
    
    # Get position state from execution engine
    position = execution_engine.position_state
    
    # Get a snapshot of the market state (atomic)
    market_snapshot = state.snapshot()
    
    # Log market state summary (every 50th update to reduce noise)
    if not hasattr(on_market_state_update, '_update_count'):
        on_market_state_update._update_count = 0
    on_market_state_update._update_count += 1
    
    # Log portfolio every 30 seconds
    current_time = time.time()
    if current_time - on_market_state_update._last_portfolio_log >= 30:
        on_market_state_update._last_portfolio_log = current_time
        elapsed = current_time - on_market_state_update._start_time
        
        # Calculate unrealized P&L
        best_ask_yes = state.get_best_ask_yes()
        best_ask_no = state.get_best_ask_no()
        unrealized_pnl = 0.0
        if position.Qy > 0 and best_ask_yes:
            # Value of YES position at current market price
            unrealized_pnl += (best_ask_yes * position.Qy) - position.Cy
        if position.Qn > 0 and best_ask_no:
            # Value of NO position at current market price
            unrealized_pnl += (best_ask_no * position.Qn) - position.Cn
        
        # Calculate average cost
        avg_cost_yes = (position.Cy / position.Qy) if position.Qy > 0 else 0.0
        avg_cost_no = (position.Cn / position.Qn) if position.Qn > 0 else 0.0
        
        logger.info(f"\n{'='*60}")
        logger.info(f"💼 PORTFOLIO UPDATE (Elapsed: {elapsed:.0f}s)")
        logger.info(f"{'='*60}")
        logger.info(f"  YES Position: {position.Qy:.2f} shares @ avg ${avg_cost_yes/1000:.3f} (cost: ${position.Cy/1000:.2f})")
        logger.info(f"  NO Position:   {position.Qn:.2f} shares @ avg ${avg_cost_no/1000:.3f} (cost: ${position.Cn/1000:.2f})")
        logger.info(f"  Total Cost:    ${(position.Cy + position.Cn)/1000:.2f}")
        if best_ask_yes and best_ask_no:
            logger.info(f"  Unrealized P&L: ${unrealized_pnl/1000:.2f}")
        logger.info(f"  Balance (Qy-Qn): {position.Qy - position.Qn:.2f}")
        logger.info(f"{'='*60}\n")
    
    if on_market_state_update._update_count % 50 == 0:
        best_ask_yes = state.get_best_ask_yes()
        best_ask_no = state.get_best_ask_no()
        time_rem = state.get_time_remaining_minutes()
        strike_info = f"Strike=${state.strike_price:,.0f}" if state.strike_price > 0 else "Strike=$0 (not set)"
        yes_str = f"{best_ask_yes:.1f}" if best_ask_yes is not None else "N/A"
        no_str = f"{best_ask_no:.1f}" if best_ask_no is not None else "N/A"
        btc_str = f"${state.btc_price:,.0f}" if state.btc_price is not None else "N/A"
        time_str = f"{time_rem:.1f}m" if time_rem is not None else "N/A"
        slug = state.slug if hasattr(state, 'slug') and state.slug else "unknown"
        logger.info(f"\n📊 Market Update #{on_market_state_update._update_count} [{slug}]: "
                   f"YES={yes_str} | NO={no_str} | "
                   f"BTC={btc_str} | {strike_info} | T={time_str}")
    
    # Evaluate strategy
    signals = evaluate_strategy(market_snapshot, position)
    
    # Log results
    if signals is None:
        # No signals - this is normal, but log occasionally for debugging
        if on_market_state_update._update_count % 100 == 0:
            logger.debug(f"No signals (update #{on_market_state_update._update_count})")
    else:
        # Highlight buy signals prominently
        slug = state.slug if hasattr(state, 'slug') and state.slug else "unknown"
        logger.info(f"\n{'='*60}")
        logger.info(f"🛒 BUY SIGNAL [{slug}]: {len(signals)} signal(s)")
        logger.info(f"{'='*60}")
        for i, signal in enumerate(signals, 1):
            logger.info(f"  [{i}] {signal.side} @ ${signal.price/1000:.3f} ({signal.price:.1f} ticks) × {signal.size:.1f} shares")
            logger.info(f"      Priority: {signal.priority} | Reason: {signal.reason}")
        
        # Execute signals via execution engine
        for signal in signals:
            try:
                order = await execution_engine.execute_signal(signal, state)
                logger.info(f"  ✅ ORDER SUBMITTED: {order.order_id} | Status: {order.status.value}")
            except Exception as e:
                logger.error(f"  ❌ ORDER FAILED: {e}", exc_info=True)
        
        # Log current position (will be updated by execution engine on fills)
        logger.info(f"\n  📊 Position: YES={position.Qy:.1f} shares (${position.Cy/1000:.2f}) | "
                   f"NO={position.Qn:.1f} shares (${position.Cn/1000:.2f})")
        logger.info(f"{'='*60}\n")


async def main():
    """Main function to run live strategy testing."""
    logger.info("Starting live strategy testing...")
    logger.info("Strategy will evaluate on every market state update")
    
    # Create execution engine
    execution_engine = ExecutionEngine(
        mode=config.EXECUTION_MODE,
        position_state=None  # Will be set after market discovery
    )
    
    # Store execution engine for callback access
    on_market_state_update._execution_engine = execution_engine
    
    # Create callback wrapper
    def market_update_callback(state: MarketState):
        # Get execution engine from closure
        exec_engine = on_market_state_update._execution_engine
        # Schedule async callback
        asyncio.create_task(on_market_state_update(state, exec_engine))
    
    # Create callback for position state resets
    def position_state_reset_callback(new_position_state: PositionState):
        """Called whenever orchestrator creates a new PositionState (market switch or init)."""
        execution_engine.set_position_state(new_position_state)
        logger.info(f"✅ Execution engine position state updated for market: {new_position_state.market_id}")
    
    orchestrator = IngestionOrchestrator(
        on_market_state_update=market_update_callback,
        on_position_state_reset=position_state_reset_callback
    )
    
    try:
        # Initialize first to check for errors
        await orchestrator.initialize()
        
        # Position state is already set via callback during initialize(), but this ensures it's set
        if orchestrator.position_state:
            execution_engine.set_position_state(orchestrator.position_state)
        
        # Check if strike price was extracted correctly
        if orchestrator.market_state and orchestrator.market_state.strike_price == 0:
            logger.warning("⚠️  WARNING: Strike price is $0 - market metadata extraction may have failed")
            logger.warning("Oracle filter will be disabled (model price calculation requires strike price)")
            logger.warning("Other strategy features (arbitrage, bootstrap, hedging) will still work")
            logger.info("Continuing anyway for testing...")
        
        # Start WebSocket connections
        logger.info("Connecting to WebSockets...")
        await orchestrator.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    except Exception as e:
        logger.error(f"Error during execution: {e}", exc_info=True)
    finally:
        await orchestrator.stop()


if __name__ == "__main__":
    asyncio.run(main())

