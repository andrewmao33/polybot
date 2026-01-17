"""
Main ingestion orchestrator.
Coordinates all WebSocket connections and state updates.
"""
import asyncio
import aiohttp
import logging
import time
from typing import Callable, Optional

from ingestion.gamma_api import (
    get_current_btc_15m_market,
    get_next_btc_15m_market,
    extract_market_metadata,
    get_ssl_context
)
from ingestion.polymarket_ws import PolymarketWebSocket
from state.market_state import MarketState
from state.position_state import PositionState

logger = logging.getLogger(__name__)


class IngestionOrchestrator:
    """
    Orchestrates all data ingestion components.
    Manages WebSocket connections and coordinates state updates.
    """
    
    def __init__(
        self,
        on_market_state_update: Optional[Callable[[MarketState], None]] = None,
        on_position_state_reset: Optional[Callable[[PositionState], None]] = None
    ):
        """
        Initialize ingestion orchestrator.
        
        Args:
            on_market_state_update: Callback called when market state updates
            on_position_state_reset: Callback called when position state is reset (new market)
        """
        self.on_market_state_update = on_market_state_update
        self.on_position_state_reset = on_position_state_reset
        
        # State objects
        self.market_state: Optional[MarketState] = None
        self.position_state: Optional[PositionState] = None
        
        # WebSocket handlers
        self.polymarket_ws: Optional[PolymarketWebSocket] = None
        
        # HTTP session
        self.session: Optional[aiohttp.ClientSession] = None
        
        # Running flag
        self.running = False

        # Market refresh task
        self.market_refresh_task: Optional[asyncio.Task] = None

        # Current market slug for timing calculations
        self.current_slug: str = ""
        
    
    async def initialize(self):
        """Initialize market discovery and state objects."""
        ssl_context = get_ssl_context()
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=ssl_context)
        )

        # Fetch current market
        logger.info("Discovering current BTC 15-minute market...")
        market_data = await get_current_btc_15m_market(self.session)
        metadata = await extract_market_metadata(market_data)

        slug = metadata.get('slug', 'unknown')
        logger.info(f"Found market: {metadata['description']} ({slug})")

        # Create MarketState
        self.market_state = MarketState(
            market_id=metadata["market_id"],
            strike_price=metadata["strike_price"],
            end_timestamp=metadata["end_timestamp"]
        )
        self.market_state.asset_id_yes = metadata["asset_id_yes"]
        self.market_state.asset_id_no = metadata["asset_id_no"]
        self.market_state.slug = slug

        # For Up/Down markets, book messages use CLOB token IDs as asset_ids
        if len(metadata["clob_token_ids"]) == 2:
            self.market_state.asset_id_yes = metadata["clob_token_ids"][0]
            self.market_state.asset_id_no = metadata["clob_token_ids"][1]

        # Create PositionState
        self.position_state = PositionState(market_id=metadata["market_id"])

        # Notify callback about new position state
        if self.on_position_state_reset:
            self.on_position_state_reset(self.position_state)

        # Store slug for timing calculations
        self.current_slug = slug

        # Create Polymarket WebSocket
        self.polymarket_ws = PolymarketWebSocket(
            market_state=self.market_state,
            clob_token_ids=metadata["clob_token_ids"],
            on_state_update=self.on_market_state_update
        )

        return metadata
    
    async def _switch_markets_periodically(self):
        """Background task to switch markets 5 seconds before each 15-minute interval."""
        while self.running:
            try:
                if not self.current_slug or not self.market_state:
                    await asyncio.sleep(10)
                    continue

                # Parse timing from slug: btc-updown-15m-1767322800
                current_start = int(self.current_slug.split("-")[-1])
                next_start = current_start + 900
                switch_time = next_start - 5  # 5 seconds early

                # Wait until switch time
                wait_time = switch_time - time.time()
                if wait_time > 0:
                    logger.info(f"Waiting {wait_time:.0f}s until market switch")
                    await asyncio.sleep(wait_time)

                if not self.running:
                    break

                # Get next market directly (no polling needed)
                logger.info("Switching to next market...")
                market_data = await get_next_btc_15m_market(self.session)
                metadata = await extract_market_metadata(market_data)
                new_slug = metadata.get('slug', 'unknown')

                # Update market state
                self.market_state.market_id = metadata["market_id"]
                self.market_state.strike_price = metadata["strike_price"]
                self.market_state.end_timestamp = metadata["end_timestamp"]
                self.market_state.slug = new_slug
                if len(metadata["clob_token_ids"]) == 2:
                    self.market_state.asset_id_yes = metadata["clob_token_ids"][0]
                    self.market_state.asset_id_no = metadata["clob_token_ids"][1]

                # Reset position state
                self.position_state = PositionState(market_id=metadata["market_id"])
                self.current_slug = new_slug

                # Notify callback (cancels orders, resets executor)
                if self.on_position_state_reset:
                    self.on_position_state_reset(self.position_state)

                # Switch WebSocket subscriptions
                if self.polymarket_ws:
                    await self.polymarket_ws.switch_markets(metadata["clob_token_ids"])

                logger.info(f"Market switched to {new_slug}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error switching markets: {e}")
                await asyncio.sleep(10)
    
    async def start(self):
        """Start all ingestion components."""
        if not self.market_state:
            await self.initialize()
        
        self.running = True
        
        logger.info("Starting ingestion components...")
        
        # Start market switching task FIRST (before WebSocket connections which block)
        self.market_refresh_task = asyncio.create_task(self._switch_markets_periodically())
        logger.info("✅ Market switching task started (will switch at 15-minute intervals)")

        # Start Polymarket WebSocket (this runs forever)
        try:
            await self.polymarket_ws.connect()
        except Exception as e:
            logger.error(f"Polymarket WebSocket connection error: {e}", exc_info=e)
    
    async def stop(self):
        """Stop all ingestion components."""
        self.running = False

        logger.info("Stopping ingestion components...")

        # Cancel market refresh task
        if self.market_refresh_task:
            self.market_refresh_task.cancel()
            try:
                await self.market_refresh_task
            except asyncio.CancelledError:
                pass

        if self.polymarket_ws:
            await self.polymarket_ws.disconnect()

        if self.session:
            await self.session.close()

        logger.info("All ingestion components stopped")
    
    def get_market_state(self) -> Optional[MarketState]:
        """Get current market state snapshot."""
        if self.market_state:
            return self.market_state.snapshot()
        return None
    
    def get_position_state(self) -> Optional[PositionState]:
        """Get current position state."""
        return self.position_state


async def run_ingestion(
    on_market_state_update: Optional[Callable[[MarketState], None]] = None
):
    """
    Convenience function to run ingestion.
    
    Args:
        on_market_state_update: Callback for market state updates
    """
    orchestrator = IngestionOrchestrator(on_market_state_update=on_market_state_update)
    
    try:
        await orchestrator.start()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        await orchestrator.stop()
    
    return orchestrator

