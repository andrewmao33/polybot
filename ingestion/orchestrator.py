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
    extract_market_metadata,
    get_ssl_context
)
from ingestion.polymarket_ws import PolymarketWebSocket
from ingestion.coinbase_ws import CoinbaseWebSocket
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
        self.coinbase_ws: Optional[CoinbaseWebSocket] = None
        
        # HTTP session
        self.session: Optional[aiohttp.ClientSession] = None
        
        # Running flag
        self.running = False
        
        # Market refresh task
        self.market_refresh_task: Optional[asyncio.Task] = None
        
        # Current market slug for next market calculation
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
        
        
        # Initialize market state and WebSocket handlers
        await self._initialize_market(metadata)
        
        # Create Coinbase WebSocket handler (only needed on first init)
        if not self.coinbase_ws:
            self.coinbase_ws = CoinbaseWebSocket(
                market_state=self.market_state,
                on_state_update=self._on_market_state_update
            )
        
        return metadata
    
    def _on_market_state_update(self, state: MarketState):
        """Internal callback when market state updates."""
        if self.on_market_state_update:
            self.on_market_state_update(state)
    
    async def _switch_markets_periodically(self):
        """Background task to switch markets at exact 15-minute intervals (start + 900 seconds)."""
        while self.running:
            try:
                if not self.current_slug or not self.market_state:
                    await asyncio.sleep(60)
                    continue
                
                # Extract timestamp from slug: btc-updown-15m-1767322800
                try:
                    current_start = int(self.current_slug.split("-")[-1])
                    next_start = current_start + 900
                    now = int(time.time())
                    
                    # Check if current market has ended (we're past the next start time)
                    if now >= next_start:
                        # Market has ended - switch immediately
                        logger.info(f"⏰ Current market has ended, switching immediately (current: {self.current_slug})")
                    else:
                        # Calculate wait time until next market starts (exactly at interval, no buffer)
                        wait_time = next_start - now
                        logger.info(f"⏰ Waiting {wait_time:.0f}s until next market switch (current: {self.current_slug})")
                        await asyncio.sleep(wait_time)
                    
                    if not self.running:
                        break
                    
                    # Get BTC price at switch time (before fetching new market)
                    btc_price_at_switch = self.market_state.btc_price if self.market_state else None
                    if btc_price_at_switch is None:
                        logger.warning("BTC price not available at switch time, will use 0")
                        btc_price_at_switch = 0.0
                    
                    logger.info(f"🔄 Switching to next market (BTC price: ${btc_price_at_switch:,.2f})")
                    
                    # Fetch current market (at switch time, the "current" market is the one that just started)
                    market_data = await get_current_btc_15m_market(self.session)
                    metadata = await extract_market_metadata(market_data)
                    
                    # Check if market has changed
                    if self.market_state.market_id == metadata["market_id"]:
                        self.current_slug = metadata.get('slug', self.current_slug)
                        await asyncio.sleep(30)
                        continue
                    
                    # Market has changed - switch to new market
                    old_slug = self.market_state.slug
                    new_slug = metadata.get('slug', 'unknown')
                    logger.info(f"🔄 Switching market: {old_slug} -> {new_slug} (strike: ${btc_price_at_switch:,.2f})")
                    
                    # Set strike price to BTC price at switch time
                    metadata["strike_price"] = btc_price_at_switch
                    
                    # Update market state with new market info
                    self.market_state.market_id = metadata["market_id"]
                    self.market_state.strike_price = btc_price_at_switch
                    self.market_state.end_timestamp = metadata["end_timestamp"]
                    self.market_state.asset_id_yes = metadata["asset_id_yes"]
                    self.market_state.asset_id_no = metadata["asset_id_no"]
                    self.market_state.slug = new_slug
                    
                    # For Up/Down markets, book messages use CLOB token IDs as asset_ids
                    if len(metadata["clob_token_ids"]) == 2:
                        self.market_state.asset_id_yes = metadata["clob_token_ids"][0]
                        self.market_state.asset_id_no = metadata["clob_token_ids"][1]
                    
                    # Reset position state for new market
                    from state.position_state import PositionState
                    self.position_state = PositionState(market_id=metadata["market_id"])
                    
                    # Notify callback about new position state
                    if self.on_position_state_reset:
                        self.on_position_state_reset(self.position_state)
                    
                    self.current_slug = new_slug
                    
                    # Switch markets using WebSocket
                    if self.polymarket_ws:
                        await self.polymarket_ws.switch_markets(metadata["clob_token_ids"])
                        logger.info("✅ Market switch complete")
                    else:
                        logger.error("Polymarket WebSocket not available for market switch")

                    
                except (ValueError, IndexError) as e:
                    logger.warning(f"Could not parse slug timestamp: {self.current_slug}, error: {e}")
                    await asyncio.sleep(60)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error switching markets: {e}", exc_info=True)
                await asyncio.sleep(60)
    
    async def _initialize_market(self, metadata: dict):
        """Initialize market state and WebSocket handlers for a given market."""
        # Initialize state objects
        self.market_state = MarketState(
            market_id=metadata["market_id"],
            strike_price=metadata["strike_price"],
            end_timestamp=metadata["end_timestamp"]
        )
        self.market_state.asset_id_yes = metadata["asset_id_yes"]
        self.market_state.asset_id_no = metadata["asset_id_no"]
        self.market_state.slug = metadata.get("slug", "unknown")
        
        # For Up/Down markets, book messages use CLOB token IDs as asset_ids
        if len(metadata["clob_token_ids"]) == 2:
            self.market_state.asset_id_yes = metadata["clob_token_ids"][0]
            self.market_state.asset_id_no = metadata["clob_token_ids"][1]
        
        # Reset position state for new market
        self.position_state = PositionState(market_id=metadata["market_id"])
        
        # Notify callback about new position state
        if self.on_position_state_reset:
            self.on_position_state_reset(self.position_state)
        
        self.current_slug = metadata.get("slug", "")
        
        # Create new Polymarket WebSocket handler
        self.polymarket_ws = PolymarketWebSocket(
            market_state=self.market_state,
            clob_token_ids=metadata["clob_token_ids"],
            on_state_update=self._on_market_state_update
        )
        
        # Coinbase WS can stay the same (just update its market_state reference)
        if self.coinbase_ws:
            self.coinbase_ws.market_state = self.market_state
            # Callback is already set during initialization, no need to update
    
    async def start(self):
        """Start all ingestion components."""
        if not self.market_state:
            await self.initialize()
        
        self.running = True
        
        logger.info("Starting ingestion components...")
        
        # Start market switching task FIRST (before WebSocket connections which block)
        self.market_refresh_task = asyncio.create_task(self._switch_markets_periodically())
        logger.info("✅ Market switching task started (will switch at 15-minute intervals)")
        
        # Start both WebSocket connections concurrently (these run forever)
        results = await asyncio.gather(
            self.polymarket_ws.connect(),
            self.coinbase_ws.connect(),
            return_exceptions=True
        )
        
        # Log any exceptions
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                ws_name = "Polymarket" if i == 0 else "Coinbase"
                logger.error(f"{ws_name} WebSocket connection error: {result}", exc_info=result)
    
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
        
        if self.coinbase_ws:
            await self.coinbase_ws.disconnect()
        
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

