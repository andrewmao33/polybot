"""
Test script for ingestion and state management.
"""
import asyncio
import logging
from ingestion.orchestrator import run_ingestion
from state.market_state import MarketState

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def on_market_state_update(state: MarketState):
    """Callback function called when market state updates."""
    logger.info("\n" + "=" * 60)
    logger.info("📊 MARKET STATE UPDATE")
    logger.info(f"Market ID: {state.market_id}")
    logger.info(f"Strike Price: ${state.strike_price:,.0f}")
    logger.info(f"BTC Price: ${state.btc_price:,.2f}" if state.btc_price else "BTC Price: None")
    logger.info(f"Exchange Timestamp: {state.exchange_timestamp}")
    logger.info(f"Sync Status: YES={state.sync_status_yes}, NO={state.sync_status_no}")
    
    time_remaining = state.get_time_remaining_minutes()
    if time_remaining is not None:
        logger.info(f"Time Remaining: {time_remaining:.2f} minutes")
    
    # YES token
    best_bid_yes = state.get_best_bid_yes()
    best_ask_yes = state.get_best_ask_yes()
    if best_bid_yes is not None and best_ask_yes is not None:
        logger.info(f"✅ YES - Bid: {best_bid_yes:.1f} ({state.get_best_bid_size_yes():.1f}) | "
                   f"Ask: {best_ask_yes:.1f} ({state.get_best_ask_size_yes():.1f})")
    else:
        logger.info(f"⚠️  YES - No order book data yet")
    
    # NO token
    best_bid_no = state.get_best_bid_no()
    best_ask_no = state.get_best_ask_no()
    if best_bid_no is not None and best_ask_no is not None:
        logger.info(f"✅ NO  - Bid: {best_bid_no:.1f} ({state.get_best_bid_size_no():.1f}) | "
                   f"Ask: {best_ask_no:.1f} ({state.get_best_ask_size_no():.1f})")
    else:
        logger.info(f"⚠️  NO  - No order book data yet")
    
    # Synthetic spread check
    if best_ask_yes is not None and best_ask_no is not None:
        spread = best_ask_yes + best_ask_no
        logger.info(f"💰 Synthetic Spread: {spread:.1f} ticks (arbitrage if < 1000)")
    
    logger.info("=" * 60 + "\n")


async def main():
    """Main test function."""
    logger.info("Starting ingestion test...")
    
    try:
        await run_ingestion(on_market_state_update=on_market_state_update)
    except KeyboardInterrupt:
        logger.info("Test interrupted by user")


if __name__ == "__main__":
    asyncio.run(main())

