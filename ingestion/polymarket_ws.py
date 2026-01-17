"""
Polymarket WebSocket ingestion handler.
Connects to Polymarket CLOB and maintains best bid/ask state.
"""
import asyncio
import json
import logging
import ssl
import websockets
from typing import Optional

from state.market_state import MarketState

logger = logging.getLogger(__name__)

POLYMARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

_ssl_context = ssl.create_default_context()
_ssl_context.check_hostname = False
_ssl_context.verify_mode = ssl.CERT_NONE


class PolymarketWebSocket:
    """
    Handles WebSocket connection to Polymarket CLOB.
    Only tracks best bid/ask - no full orderbook depth.
    """

    def __init__(self, market_state: MarketState, clob_token_ids: list[str], on_state_update=None):
        self.market_state = market_state
        self.clob_token_ids = clob_token_ids
        self.on_state_update = on_state_update

        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.running = False
        self._should_reconnect = True
        self.reconnect_delay = 1.0
        self.max_reconnect_delay = 60.0

        # Track if we've received initial books
        self._got_initial_books = False

    async def connect(self):
        """Establish WebSocket connection and subscribe to markets."""
        self._should_reconnect = True
        while self._should_reconnect:
            try:
                logger.info("Connecting to Polymarket WebSocket...")
                self.ws = await websockets.connect(
                    POLYMARKET_WS_URL,
                    ssl=_ssl_context,
                    ping_interval=20,
                    ping_timeout=10
                )

                # Subscribe with custom_feature_enabled for best_bid_ask messages
                subscribe_message = {
                    "assets_ids": self.clob_token_ids,
                    "operation": "subscribe",
                    "custom_feature_enabled": True
                }

                await self.ws.send(json.dumps(subscribe_message))
                logger.info(f"Subscribed to assets: {self.clob_token_ids}")

                self.running = True
                self.reconnect_delay = 1.0

                await self._handle_messages()

            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket connection closed, reconnecting...")
                self.running = False
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)

            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                self.running = False
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, self.max_reconnect_delay)

    async def _handle_messages(self):
        """Process incoming WebSocket messages."""
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)
                    self._process_message(data)
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse message: {e}")
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Connection closed during message handling")
            self.running = False

    def _process_message(self, data):
        """Process a single WebSocket message."""
        # Handle list of books (first subscription response only)
        if isinstance(data, list):
            if not self._got_initial_books and len(data) >= 2:
                self._handle_initial_books(data)
            return

        # Handle best_bid_ask updates
        if isinstance(data, dict) and data.get("event_type") == "best_bid_ask":
            self._handle_best_bid_ask(data)

    def _handle_initial_books(self, books: list):
        """Handle initial book snapshots - process once on subscription."""
        for book in books:
            if book.get("event_type") != "book":
                continue

            asset_id = book.get("asset_id")
            if not asset_id:
                continue

            is_yes = self._is_yes_token(asset_id)
            if is_yes is None:
                continue

            bids = book.get("bids", [])
            asks = book.get("asks", [])

            # Best bid (highest) is LAST element, best ask (lowest) is LAST element
            best_bid = float(bids[-1]["price"]) * 1000 if bids else None
            best_ask = float(asks[-1]["price"]) * 1000 if asks else None

            if is_yes:
                self.market_state.best_bid_yes = best_bid
                self.market_state.best_ask_yes = best_ask
                self.market_state.sync_status_yes = True
            else:
                self.market_state.best_bid_no = best_bid
                self.market_state.best_ask_no = best_ask
                self.market_state.sync_status_no = True

            timestamp = book.get("timestamp")
            if timestamp:
                self.market_state.exchange_timestamp = int(timestamp)

            side = "YES" if is_yes else "NO"
            logger.info(f"Initial book {side}: bid={best_bid} ask={best_ask}")

        self._got_initial_books = True
        if self.market_state.sync_status:
            logger.info("Both sides synced")

    def _handle_best_bid_ask(self, data: dict):
        """Handle best_bid_ask update - only notify if values changed."""
        asset_id = data.get("asset_id")
        if not asset_id:
            return

        is_yes = self._is_yes_token(asset_id)
        if is_yes is None:
            return

        best_bid = data.get("best_bid")
        best_ask = data.get("best_ask")

        if best_bid is not None:
            best_bid = float(best_bid) * 1000
        if best_ask is not None:
            best_ask = float(best_ask) * 1000

        changed = False
        if is_yes:
            if best_bid is not None and best_bid != self.market_state.best_bid_yes:
                self.market_state.best_bid_yes = best_bid
                changed = True
            if best_ask is not None and best_ask != self.market_state.best_ask_yes:
                self.market_state.best_ask_yes = best_ask
                changed = True
        else:
            if best_bid is not None and best_bid != self.market_state.best_bid_no:
                self.market_state.best_bid_no = best_bid
                changed = True
            if best_ask is not None and best_ask != self.market_state.best_ask_no:
                self.market_state.best_ask_no = best_ask
                changed = True

        timestamp = data.get("timestamp")
        if timestamp:
            self.market_state.exchange_timestamp = int(timestamp)

        if changed and self.on_state_update:
            self.on_state_update(self.market_state)

    def _is_yes_token(self, asset_id: str) -> Optional[bool]:
        """Determine if asset_id is YES token. Returns None if unknown."""
        if len(self.clob_token_ids) == 2:
            if asset_id == self.clob_token_ids[0]:
                return True
            elif asset_id == self.clob_token_ids[1]:
                return False
        return None

    async def disconnect(self):
        """Close WebSocket connection."""
        self._should_reconnect = False
        self.running = False
        if self.ws:
            await self.ws.close()
            logger.info("Disconnected from Polymarket WebSocket")

    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self.running and self.ws is not None

    async def switch_markets(self, new_clob_token_ids: list[str]):
        """Switch to a new market."""
        if not self.is_connected():
            logger.warning("Cannot switch markets: WebSocket not connected")
            return

        old_ids = self.clob_token_ids

        # Reset state
        self.market_state.sync_status_yes = False
        self.market_state.sync_status_no = False
        self.market_state.best_bid_yes = None
        self.market_state.best_ask_yes = None
        self.market_state.best_bid_no = None
        self.market_state.best_ask_no = None
        self._got_initial_books = False

        # Unsubscribe and subscribe
        await self.ws.send(json.dumps({"assets_ids": old_ids, "operation": "unsubscribe"}))
        await self.ws.send(json.dumps({
            "assets_ids": new_clob_token_ids,
            "operation": "subscribe",
            "custom_feature_enabled": True
        }))

        self.clob_token_ids = new_clob_token_ids
        logger.info(f"Market switched: {old_ids} -> {new_clob_token_ids}")
