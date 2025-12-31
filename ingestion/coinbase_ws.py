"""
Coinbase WebSocket handler for BTC oracle data.
Connects to Coinbase ticker stream for real-time BTC price.
"""
import asyncio
import json
import logging
import ssl
import websockets
from typing import Callable, Optional

from state.market_state import MarketState

logger = logging.getLogger(__name__)

COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"

# SSL context (disable verification for testing)
_ssl_context = ssl.create_default_context()
_ssl_context.check_hostname = False
_ssl_context.verify_mode = ssl.CERT_NONE


class CoinbaseWebSocket:
    """
    Handles WebSocket connection to Coinbase for BTC price oracle.
    Updates MarketState with real-time BTC price.
    """
    
    def __init__(
        self,
        market_state: MarketState,
        on_price_update: Optional[Callable[[float], None]] = None
    ):
        """
        Initialize Coinbase WebSocket handler.
        
        Args:
            market_state: MarketState object to update
            on_price_update: Optional callback when price updates
        """
        self.market_state = market_state
        self.on_price_update = on_price_update
        
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False
        self.reconnect_delay = 1.0
        self.max_reconnect_delay = 60.0
    
    async def connect(self):
        """Establish WebSocket connection to Coinbase."""
        while not self.running:
            try:
                logger.info("Connecting to Coinbase WebSocket for BTC price")
                self.ws = await websockets.connect(
                    COINBASE_WS_URL,
                    ssl=_ssl_context,
                    ping_interval=20,
                    ping_timeout=10
                )
                
                self.running = True
                self.reconnect_delay = 1.0
                logger.info("✅ Connected to Coinbase WebSocket")
                
                # Subscribe to BTC-USD ticker
                subscribe_msg = {
                    "type": "subscribe",
                    "product_ids": ["BTC-USD"],
                    "channels": ["ticker"]
                }
                await self.ws.send(json.dumps(subscribe_msg))
                logger.info("Subscribed to BTC-USD ticker")
                
                # Start message handler
                await self._handle_messages()
                
            except websockets.exceptions.ConnectionClosed:
                logger.warning("Coinbase WebSocket connection closed, reconnecting...")
                self.running = False
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)
                
            except Exception as e:
                logger.error(f"Coinbase WebSocket error: {e}", exc_info=True)
                self.running = False
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)
    
    async def _handle_messages(self):
        """Process incoming WebSocket messages."""
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                    await self._process_message(data)
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse Coinbase message: {e}")
                except Exception as e:
                    logger.error(f"Error processing Coinbase message: {e}", exc_info=True)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Coinbase connection closed during message handling")
            self.running = False
        except Exception as e:
            logger.error(f"Error in Coinbase message handler: {e}", exc_info=True)
            self.running = False
    
    async def _process_message(self, data: dict):
        """
        Process ticker message from Coinbase.
        
        Expected format:
        {
            "type": "ticker",
            "price": "100000.00",
            "product_id": "BTC-USD",
            ...
        }
        """
        message_type = data.get("type")
        
        if message_type == "ticker":
            price_str = data.get("price")
            if price_str:
                try:
                    price = float(price_str)
                    if price > 0:
                        self.market_state.btc_price = price
                        logger.debug(f"BTC price updated: ${price:,.2f}")
                        
                        # Notify callback if provided
                        if self.on_price_update:
                            self.on_price_update(price)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid price value: {price_str}")
        elif message_type == "subscriptions":
            logger.debug(f"Subscription confirmed: {data}")
        else:
            logger.debug(f"Unknown Coinbase message type: {message_type}")
    
    async def disconnect(self):
        """Close WebSocket connection."""
        self.running = False
        if self.ws:
            await self.ws.close()
            logger.info("Disconnected from Coinbase WebSocket")
    
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self.running and self.ws is not None and not self.ws.closed

