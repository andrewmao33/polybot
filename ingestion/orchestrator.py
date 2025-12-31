"""
Main ingestion orchestrator.
Coordinates all WebSocket connections and state updates.
"""
import asyncio
import aiohttp
import logging
from typing import Callable, Optional
from datetime import datetime

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
        on_market_state_update: Optional[Callable[[MarketState], None]] = None
    ):
        """
        Initialize ingestion orchestrator.
        
        Args:
            on_market_state_update: Callback called when market state updates
        """
        self.on_market_state_update = on_market_state_update
        
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
        
        logger.info(f"Found market: {metadata['description']}")
        logger.info(f"Market ID: {metadata['market_id']}")
        logger.info(f"Strike Price: ${metadata['strike_price']:,.0f} (will be set when book syncs)")
        logger.info(f"Active: {metadata['active']}, Closed: {metadata['closed']}")
        
        # Initialize market state and WebSocket handlers
        await self._initialize_market(metadata)
        
        # Create Coinbase WebSocket handler (only needed on first init)
        if not self.coinbase_ws:
            self.coinbase_ws = CoinbaseWebSocket(
                market_state=self.market_state,
                on_price_update=self._on_btc_price_update  # Set strike for Up/Down markets
            )
        
        return metadata
    
    def _on_btc_price_update(self, btc_price: float):
        """Callback when BTC price updates."""
        # If books are already synced but strike not set, set it now
        if self.market_state and self.market_state.sync_status and self.market_state.strike_price == 0 and not self.strike_set_from_book:
            self.market_state.strike_price = btc_price
            self.strike_set_from_book = True
            logger.info(f"✅ Set strike price to BTC price: ${btc_price:,.2f} (market already synced)")
    
    def _on_first_book_sync(self):
        """Called when both order books are synced for the first time."""
        if self.market_state and self.market_state.strike_price == 0 and not self.strike_set_from_book:
            # For Up/Down markets, set strike to BTC price when market starts (first book sync)
            if self.market_state.btc_price:
                self.market_state.strike_price = self.market_state.btc_price
                self.strike_set_from_book = True
                logger.info(f"✅ Market started - Set strike price to BTC price at market start: ${self.market_state.btc_price:,.2f}")
            else:
                logger.warning("Book synced but BTC price not available yet - will set strike when BTC price arrives")
    
    def _on_market_state_update(self, state: MarketState):
        """Internal callback when market state updates."""
        # Check if this is the first time both books are synced
        if state.sync_status and not self.strike_set_from_book:
            self._on_first_book_sync()
        
        if self.on_market_state_update:
            self.on_market_state_update(state)
    
    async def _refresh_market_periodically(self):
        """Background task to refresh market every 15 minutes."""
        while self.running:
            try:
                # Wait 15 minutes (900 seconds)
                await asyncio.sleep(900)
                
                if not self.running:
                    break
                
                logger.info("🔄 Checking for new market (15-minute interval)...")
                
                # Fetch current market
                market_data = await get_current_btc_15m_market(self.session)
                metadata = await extract_market_metadata(market_data)
                
                # Check if market has changed
                if self.market_state and self.market_state.market_id == metadata["market_id"]:
                    logger.info(f"✅ Still on same market: {metadata['market_id']}")
                    continue
                
                # Market has changed - switch to new market
                logger.info(f"🔄 Market changed! Old: {self.market_state.market_id if self.market_state else 'None'}")
                logger.info(f"🔄 New market: {metadata['market_id']}")
                logger.info(f"   Description: {metadata['description']}")
                
                # Disconnect from old market
                logger.info("Disconnecting from old market...")
                if self.polymarket_ws:
                    await self.polymarket_ws.disconnect()
                # Coinbase WS can stay connected (same BTC price feed)
                
                # Initialize new market
                logger.info("Initializing new market...")
                await self._initialize_market(metadata)
                
                # Reconnect Polymarket WebSocket for new market
                logger.info("Connecting to new market...")
                await self.polymarket_ws.connect()
                
                logger.info("✅ Successfully switched to new market!")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error refreshing market: {e}", exc_info=True)
                # Continue running even if refresh fails
                await asyncio.sleep(60)  # Wait 1 minute before retrying
    
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
        
        # For Up/Down markets, book messages use CLOB token IDs as asset_ids
        if len(metadata["clob_token_ids"]) == 2:
            self.market_state.asset_id_yes = metadata["clob_token_ids"][0]
            self.market_state.asset_id_no = metadata["clob_token_ids"][1]
            logger.info(f"Using CLOB token IDs as asset IDs - YES: {self.market_state.asset_id_yes}, NO: {self.market_state.asset_id_no}")
        
        # Reset strike tracking
        self.strike_set_from_book = False
        
        # Reset position state for new market
        self.position_state = PositionState(market_id=metadata["market_id"])
        logger.info("📍 Position state reset for new market")
        
        # Create new Polymarket WebSocket handler
        self.polymarket_ws = PolymarketWebSocket(
            market_state=self.market_state,
            clob_token_ids=metadata["clob_token_ids"],
            on_state_update=self._on_market_state_update
        )
        
        # Coinbase WS can stay the same (just update its market_state reference)
        if self.coinbase_ws:
            self.coinbase_ws.market_state = self.market_state
    
    async def start(self):
        """Start all ingestion components."""
        if not self.market_state:
            await self.initialize()
        
        self.running = True
        
        logger.info("Starting ingestion components...")
        
        # Start both WebSocket connections concurrently
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
        
        # Start market refresh task
        self.market_refresh_task = asyncio.create_task(self._refresh_market_periodically())
        logger.info("✅ Market refresh task started (will check every 15 minutes)")
    
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

