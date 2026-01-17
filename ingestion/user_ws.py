"""
Polymarket User Channel WebSocket handler.
Receives real-time fill notifications with actual execution prices.
"""
import asyncio
import json
import logging
import ssl
import websockets
from typing import Callable, Optional, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)

USER_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"

# SSL context
_ssl_context = ssl.create_default_context()
_ssl_context.check_hostname = False
_ssl_context.verify_mode = ssl.CERT_NONE


@dataclass
class FillEvent:
    """Represents a fill event from the user channel."""
    order_id: str
    asset_id: str
    side: str  # "BUY" or "SELL"
    price: float  # Actual execution price (decimal 0.0-1.0)
    size: float  # Fill size
    market_id: str
    status: str  # "MATCHED", "MINED", "CONFIRMED"
    timestamp: str
    is_maker: bool  # True if we were the maker


class UserWebSocket:
    """
    Handles WebSocket connection to Polymarket User Channel.
    Receives real-time trade fill notifications.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        maker_address: str,
        on_fill: Optional[Callable[[FillEvent], None]] = None
    ):
        """
        Initialize User WebSocket handler.

        Args:
            api_key: CLOB API key
            api_secret: CLOB API secret
            api_passphrase: CLOB API passphrase
            maker_address: Our wallet address (to detect maker fills)
            on_fill: Callback function called when a fill occurs
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self.maker_address = maker_address.lower()
        self.on_fill = on_fill

        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False
        self._should_reconnect = True
        self.reconnect_delay = 1.0
        self.max_reconnect_delay = 60.0

        # Current market filter (optional)
        self.current_market_id: Optional[str] = None

    async def connect(self):
        """Establish WebSocket connection and authenticate."""
        self._should_reconnect = True
        while self._should_reconnect:
            try:
                logger.info("Connecting to Polymarket User WebSocket...")
                self.ws = await websockets.connect(
                    USER_WS_URL,
                    ssl=_ssl_context,
                    ping_interval=20,
                    ping_timeout=10
                )

                # Send authentication message
                auth_message = {
                    "type": "user",
                    "auth": {
                        "apiKey": self.api_key,
                        "secret": self.api_secret,
                        "passphrase": self.api_passphrase
                    }
                }

                await self.ws.send(json.dumps(auth_message))
                logger.info("Authenticated to User WebSocket")

                self.running = True
                self.reconnect_delay = 1.0

                # Start message handler
                await self._handle_messages()

            except websockets.exceptions.ConnectionClosed:
                logger.warning("User WebSocket connection closed, reconnecting...")
                self.running = False
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)

            except Exception as e:
                logger.error(f"User WebSocket error: {e}", exc_info=True)
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
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON message: {message[:100]}")
                except Exception as e:
                    logger.error(f"Error processing message: {e}")

        except websockets.exceptions.ConnectionClosed:
            logger.warning("User WebSocket connection closed during message handling")
            self.running = False

    async def _process_message(self, data: Dict[str, Any]):
        """Process a single WebSocket message."""
        event_type = data.get("event_type")
        if event_type == "trade":
            await self._handle_trade(data)
        elif event_type == "order":
            # Order status updates (can log but not critical)
            logger.debug(f"Order update: {data.get('id')} -> {data.get('status')}")
        elif event_type in ("ping", "pong", "heartbeat"):
            pass  # Ignore heartbeats
        else:
            logger.debug(f"User WS message type: {event_type}")

    async def _handle_trade(self, data: Dict[str, Any]):
        """Handle a trade/fill message."""
        status = data.get("status")
        trader_side = data.get("trader_side", "")
        logger.debug(f"[WS DEBUG] Trade received: status={status} trader_side={trader_side}")

        if status != "MATCHED":
            return

        try:

            if trader_side == "TAKER":
                # My order crossed the spread and filled as taker
                price = float(data.get("price", 0))
                size = float(data.get("size", 0))
                asset_id = data.get("asset_id", "")
                order_id = data.get("taker_order_id", "")
            elif trader_side == "MAKER":
                # My order was resting and got matched - find my order in maker_orders
                found = False
                for maker in data.get("maker_orders", []):
                    if maker.get("maker_address", "").lower() == self.maker_address:
                        price = float(maker.get("price", 0))
                        size = float(maker.get("matched_amount", 0))
                        asset_id = maker.get("asset_id", "")
                        order_id = maker.get("order_id", "")
                        found = True
                        break
                if not found:
                    return
            else:
                return

            fill = FillEvent(
                order_id=order_id,
                asset_id=asset_id,
                side="BUY",
                price=price,
                size=size,
                market_id=data.get("market", ""),
                status="MATCHED",
                timestamp=data.get("timestamp", ""),
                is_maker=(trader_side == "MAKER")
            )

            logger.info(f"[WS FILL] {trader_side} {fill.size:.1f} @ ${fill.price:.2f} asset={fill.asset_id[:20]}...")

            if self.on_fill:
                logger.debug("[WS DEBUG] Calling on_fill callback")
                self.on_fill(fill)
            else:
                logger.debug("[WS DEBUG] No on_fill callback set!")

        except Exception as e:
            logger.error(f"Error parsing trade: {e}")

    def set_market(self, market_id: str):
        """Set current market filter."""
        self.current_market_id = market_id

    async def disconnect(self):
        """Close WebSocket connection."""
        self._should_reconnect = False
        self.running = False
        if self.ws:
            await self.ws.close()
            logger.info("Disconnected from User WebSocket")

    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self.running and self.ws is not None and not self.ws.closed
