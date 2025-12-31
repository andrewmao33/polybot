"""
Polymarket WebSocket ingestion handler.
Connects to Polymarket CLOB and maintains real-time order book state.
"""
import asyncio
import json
import logging
import ssl
import websockets
from typing import Callable, Optional

from state.market_state import MarketState

logger = logging.getLogger(__name__)

POLYMARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# SSL context for testing
_ssl_context = ssl.create_default_context()
_ssl_context.check_hostname = False
_ssl_context.verify_mode = ssl.CERT_NONE


class PolymarketWebSocket:
    """
    Handles WebSocket connection to Polymarket CLOB.
    Maintains order book state and notifies on updates.
    """
    
    def __init__(
        self,
        market_state: MarketState,
        clob_token_ids: list[str],
        on_state_update: Optional[Callable[[MarketState], None]] = None
    ):
        """
        Initialize Polymarket WebSocket handler.
        
        Args:
            market_state: MarketState object to update
            clob_token_ids: List of CLOB token IDs to subscribe to
            on_state_update: Callback function called when state updates
        """
        self.market_state = market_state
        self.clob_token_ids = clob_token_ids
        self.on_state_update = on_state_update
        
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False
        self.reconnect_delay = 1.0
        self.max_reconnect_delay = 60.0
    
    async def connect(self):
        """Establish WebSocket connection and subscribe to markets."""
        while not self.running:
            try:
                logger.info(f"Connecting to Polymarket WebSocket for market {self.market_state.market_id}")
                self.ws = await websockets.connect(
                    POLYMARKET_WS_URL,
                    ssl=_ssl_context,
                    ping_interval=20,
                    ping_timeout=10
                )
                
                # Subscribe to tokens
                subscribe_message = {
                    "assets_ids": self.clob_token_ids,
                    "type": "market"
                }
                
                await self.ws.send(json.dumps(subscribe_message))
                logger.info(f"Subscribed to assets: {self.clob_token_ids}")
                
                self.running = True
                self.reconnect_delay = 1.0
                
                # Start message handler
                await self._handle_messages()
                
            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket connection closed, reconnecting...")
                self.running = False
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)
                
            except Exception as e:
                logger.error(f"WebSocket error: {e}", exc_info=True)
                self.running = False
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)
    
    async def _handle_messages(self):
        """Process incoming WebSocket messages."""
        message_count = 0
        try:
            async for message in self.ws:
                message_count += 1
                try:
                    data = json.loads(message)
                    
                    # Log every message for debugging (first 10, then every 50th)
                    if message_count <= 10 or message_count % 50 == 0:
                        logger.info(f"📨 Message #{message_count}: {json.dumps(data)[:200]}...")
                    
                    await self._process_message(data)
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse message: {e}")
                except Exception as e:
                    logger.error(f"Error processing message: {e}", exc_info=True)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Connection closed during message handling")
            self.running = False
        except Exception as e:
            logger.error(f"Error in message handler: {e}", exc_info=True)
            self.running = False
    
    async def _process_message(self, data):
        """Process a single WebSocket message."""
        # Handle list messages (subscription confirmation with book snapshots)
        if isinstance(data, list):
            logger.info(f"✅ Subscription confirmation received with {len(data)} book(s)")
            # Process each book in the list
            for i, book_data in enumerate(data):
                if isinstance(book_data, dict) and book_data.get("event_type") == "book":
                    asset_id = book_data.get("asset_id")
                    logger.debug(f"Processing book {i+1}: asset_id={asset_id}, YES={self.market_state.asset_id_yes}, NO={self.market_state.asset_id_no}")
                    await self._handle_book_message(book_data)
                else:
                    logger.debug(f"Skipping item {i+1}: not a book message (type={type(book_data)}, event_type={book_data.get('event_type') if isinstance(book_data, dict) else 'N/A'})")
            return
        
        # Handle dict messages
        if not isinstance(data, dict):
            logger.warning(f"Unexpected message type: {type(data)}")
            return
        
        event_type = data.get("event_type")
        
        # Check for different message formats
        if event_type == "book":
            await self._handle_book_message(data)
        elif event_type == "price_change":
            await self._handle_price_change_message(data)
        else:
            logger.debug(f"Unknown event type: {event_type}, keys: {list(data.keys())[:5]}")
    
    async def _handle_book_message(self, data: dict):
        """
        Handle snapshot (book) message.
        Clears local book and populates with new snapshot.
        """
        asset_id = data.get("asset_id")
        timestamp = data.get("timestamp")
        hash_value = data.get("hash")
        
        if not asset_id:
            logger.warning("Book message missing asset_id")
            return
        
        # Determine which side (YES or NO)
        # Book messages use CLOB token IDs as asset_ids
        # Match against clob_token_ids (first = YES, second = NO typically)
        if len(self.clob_token_ids) == 2:
            if asset_id == self.clob_token_ids[0]:
                is_yes = True
            elif asset_id == self.clob_token_ids[1]:
                is_yes = False
            else:
                logger.warning(f"Unknown asset_id in book: {asset_id} (expected {self.clob_token_ids})")
                return
        else:
            # Fallback to asset_id_yes/no matching
            is_yes = asset_id == self.market_state.asset_id_yes
            if asset_id not in [self.market_state.asset_id_yes, self.market_state.asset_id_no]:
                logger.warning(f"Ignoring book message for unknown asset: {asset_id}")
                return
        
        # Clear existing book for this asset
        if is_yes:
            self.market_state.order_book_yes_bids.clear()
            self.market_state.order_book_yes_asks.clear()
        else:
            self.market_state.order_book_no_bids.clear()
            self.market_state.order_book_no_asks.clear()
        
        # Book format: bids/asks are arrays of dicts with "price" and "size" as strings
        # Prices are in decimal format (0.48 = 480 ticks, 0.52 = 520 ticks)
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        
        # Populate bids
        for level in bids:
            if isinstance(level, dict):
                price_decimal = float(level.get("price", 0))
                # Convert decimal to ticks: 0.48 -> 480, 0.52 -> 520
                price = price_decimal * 1000
                size = float(level.get("size", 0))
                
                if price > 0 and size > 0:
                    if is_yes:
                        self.market_state.order_book_yes_bids[price] = size
                    else:
                        self.market_state.order_book_no_bids[price] = size
        
        # Populate asks
        for level in asks:
            if isinstance(level, dict):
                price_decimal = float(level.get("price", 0))
                # Convert decimal to ticks: 0.48 -> 480, 0.52 -> 520
                price = price_decimal * 1000
                size = float(level.get("size", 0))
                
                if price > 0 and size > 0:
                    if is_yes:
                        self.market_state.order_book_yes_asks[price] = size
                    else:
                        self.market_state.order_book_no_asks[price] = size
        
        # Update timestamp and clock skew
        if timestamp:
            self.market_state.exchange_timestamp = int(timestamp)
            self.market_state.update_clock_skew()
        
        # Mark this side as synced
        if is_yes:
            was_synced = self.market_state.sync_status
            self.market_state.sync_status_yes = True
            logger.info(f"✅ YES book snapshot received (hash: {hash_value})")
            if not was_synced and self.market_state.sync_status:
                logger.info("🎯 Both books now synced! (YES + NO)")
        else:
            was_synced = self.market_state.sync_status
            self.market_state.sync_status_no = True
            logger.info(f"✅ NO book snapshot received (hash: {hash_value})")
            if not was_synced and self.market_state.sync_status:
                logger.info("🎯 Both books now synced! (YES + NO)")
        
        # Notify state update
        if self.on_state_update:
            self.on_state_update(self.market_state.snapshot())
    
    async def _handle_price_change_message(self, data: dict):
        """
        Handle delta (price_change) message.
        Updates specific price levels in the order book.
        """
        if not self.market_state.sync_status:
            # Normal at startup - just wait for book snapshots
            logger.debug("Received price_change before book snapshots (waiting for initial books...)")
            return
        
        timestamp = data.get("timestamp")
        price_changes = data.get("price_changes", [])
        
        if not price_changes:
            logger.debug("Price change message has no price_changes")
            return
        
        for change in price_changes:
            asset_id = change.get("asset_id")
            price_decimal = float(change.get("price", 0))
            # Convert decimal to ticks: 0.5 -> 500
            price = price_decimal * 1000
            size = float(change.get("size", 0))
            side = change.get("side")  # "BUY" or "SELL"
            # Optional: best_bid and best_ask are also provided for validation
            best_bid = change.get("best_bid")
            best_ask = change.get("best_ask")
            
            # Match asset_id against clob_token_ids (first = YES, second = NO)
            if len(self.clob_token_ids) == 2:
                if asset_id == self.clob_token_ids[0]:
                    is_yes = True
                elif asset_id == self.clob_token_ids[1]:
                    is_yes = False
                else:
                    continue  # Skip unknown asset
            else:
                # Fallback to asset_id_yes/no matching
                if asset_id not in [self.market_state.asset_id_yes, self.market_state.asset_id_no]:
                    continue
                is_yes = asset_id == self.market_state.asset_id_yes
            
            # Update the appropriate order book
            if side == "BUY":
                # Update bids
                if is_yes:
                    if size > 0:
                        self.market_state.order_book_yes_bids[price] = size
                    else:
                        self.market_state.order_book_yes_bids.pop(price, None)
                else:
                    if size > 0:
                        self.market_state.order_book_no_bids[price] = size
                    else:
                        self.market_state.order_book_no_bids.pop(price, None)
            else:  # SELL
                # Update asks
                if is_yes:
                    if size > 0:
                        self.market_state.order_book_yes_asks[price] = size
                    else:
                        self.market_state.order_book_yes_asks.pop(price, None)
                else:
                    if size > 0:
                        self.market_state.order_book_no_asks[price] = size
                    else:
                        self.market_state.order_book_no_asks.pop(price, None)
        
        # Update timestamp
        if timestamp:
            self.market_state.exchange_timestamp = int(timestamp)
            self.market_state.update_clock_skew()
        
        # Notify state update
        if self.on_state_update:
            self.on_state_update(self.market_state.snapshot())
    
    async def disconnect(self):
        """Close WebSocket connection."""
        self.running = False
        if self.ws:
            await self.ws.close()
            logger.info("Disconnected from Polymarket WebSocket")
    
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self.running and self.ws is not None and not self.ws.closed

