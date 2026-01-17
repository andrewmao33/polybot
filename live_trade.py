#!/usr/bin/env python3
"""
Live Trading for Polymarket.

Connects to live market data and submits REAL orders.
WARNING: This uses real money! Start with small amounts.

Usage:
    export POLYMARKET_PRIVATE_KEY="0x..."
    python live_trade.py              # Run indefinitely
    python live_trade.py --markets 1  # Run for 1 market then exit
    python live_trade.py -n 3         # Run for 3 markets then exit
    python live_trade.py -s 60        # Run for 60 seconds then exit
"""
import argparse
import asyncio
import logging
import os
import signal
import time
from datetime import datetime

import aiohttp
import ssl

from ingestion.orchestrator import IngestionOrchestrator
from ingestion.user_ws import UserWebSocket, FillEvent
from state.market_state import MarketState
from state.position_state import PositionState
from execution.real_executor import RealExecutor
from execution.order_manager import OrderManager
import config

# Configure logging - console and file
from pathlib import Path

log_dir = Path(__file__).parent / "live_trades"
log_dir.mkdir(exist_ok=True)
log_file = log_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file)
    ]
)
logger = logging.getLogger(__name__)
logger.info(f"Logging to {log_file}")

# Reduce noise from libraries
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("py_clob_client").setLevel(logging.WARNING)


class LiveTrader:
    """
    Live trading bot that submits real orders to Polymarket.
    """

    def __init__(self, private_key: str, proxy_wallet: str = "", max_markets: int = None, max_seconds: int = None):
        self.private_key = private_key
        self.proxy_wallet = proxy_wallet
        self.orchestrator: IngestionOrchestrator = None
        self.executor: RealExecutor = None
        self.order_manager: OrderManager = None
        self.position: PositionState = None
        self.user_ws: UserWebSocket = None

        # Timing
        self.last_refresh_ms = 0
        self.last_sync_ms = 0
        self.market_start_time = 0
        self.trading_start_time = 0

        # Stats
        self.markets_traded = 0
        self.total_pnl = 0.0

        # Skip first market - only trade after a market switch
        self.skip_first_market = True
        self.trading_enabled = False
        self.markets_seen = 0

        # Limits
        self.max_markets = max_markets
        self.max_seconds = max_seconds
        self.should_stop = False

    async def start(self):
        """Start live trading."""
        logger.info("=" * 60)
        logger.info("LIVE TRADING MODE - REAL MONEY")
        logger.info("=" * 60)
        logger.info("Triple Gate Parameters:")
        logger.info(f"  MAX_POSITION: {config.MAX_POSITION} shares")
        logger.info(f"  BASE_SIZE: {config.BASE_SIZE} shares")
        logger.info(f"  BASE_MARGIN: {config.BASE_MARGIN_TICKS} ticks ({config.BASE_MARGIN_TICKS/10:.1f}¢)")
        logger.info(f"  GAMMA: {config.GAMMA} (skew sensitivity)")
        logger.info(f"  LADDER_DEPTH: {config.LADDER_DEPTH} rungs")
        logger.info(f"  SLIPPAGE_TOL: {config.SLIPPAGE_TOL_TICKS} ticks ({config.SLIPPAGE_TOL_TICKS/10:.0f}¢)")
        logger.info(f"  HYSTERESIS: {config.HYSTERESIS:.0%}")
        logger.info(f"  PROFIT_LOCK_MIN: ${config.PROFIT_LOCK_MIN:.2f}")
        logger.info(f"  CIRCUIT_BREAKER: ${config.CIRCUIT_BREAKER_USD:.2f}")
        logger.info("=" * 60)
        if self.max_markets:
            logger.info(f"Will stop after {self.max_markets} market(s).")
        if self.max_seconds:
            logger.info(f"Will stop after {self.max_seconds} seconds.")
        logger.info("=" * 60)

        # Create orchestrator with callbacks
        self.orchestrator = IngestionOrchestrator(
            on_market_state_update=self._on_market_update,
            on_position_state_reset=self._on_market_switch
        )

        # Initialize (fetches current market)
        await self.orchestrator.initialize()

        # Set up position and executor
        self.position = self.orchestrator.position_state
        self.executor = RealExecutor(
            position=self.position,
            private_key=self.private_key,
            proxy_wallet=self.proxy_wallet
        )
        self.order_manager = OrderManager(self.executor)

        # Set token IDs for executor
        market = self.orchestrator.market_state
        self.executor.set_token_ids(market.asset_id_yes, market.asset_id_no, market.market_id)

        # Set up User WebSocket for push fill notifications
        # This gives us real-time fills with actual execution prices
        try:
            creds = self.executor.get_api_credentials()
            self.user_ws = UserWebSocket(
                api_key=creds["apiKey"],
                api_secret=creds["secret"],
                api_passphrase=creds["passphrase"],
                maker_address=config.PROXY_WALLET,
                on_fill=self._on_ws_fill
            )
            self.user_ws.set_market(market.market_id)
            logger.info("User WebSocket configured for fill notifications")
        except Exception as e:
            logger.warning(f"Could not set up User WebSocket: {e}")
            self.user_ws = None

        # CRITICAL: Cancel any stale orders from previous sessions BEFORE trading
        # If bot was killed ungracefully, old orders may still be on exchange
        logger.info("Cancelling any stale orders from previous sessions...")
        try:
            self.executor.cancel_all_orders()
            logger.info("✅ Stale orders cleared")
        except Exception as e:
            logger.warning(f"Could not clear stale orders: {e}")

        self.market_start_time = time.time()
        logger.info(f"Market: {market.slug}")
        logger.info(f"Strike: ${market.strike_price:,.0f}")
        logger.info(f"Token YES: {market.asset_id_yes}")
        logger.info(f"Token NO: {market.asset_id_no}")
        if self.skip_first_market:
            logger.info("⏳ WAITING for next market (skipping first market)...")
        else:
            logger.info("Waiting for orderbook sync...")

        # Set up signal handlers for clean shutdown
        # Use Python's native signal.signal() instead of asyncio's add_signal_handler
        # because asyncio handlers may not fire when loop is blocked on WebSocket I/O
        def force_exit(signum, frame):
            logger.info("Received signal, forcing immediate exit...")
            os._exit(0)

        signal.signal(signal.SIGINT, force_exit)
        signal.signal(signal.SIGTERM, force_exit)

        # Start User WebSocket in background if available
        user_ws_task = None
        if self.user_ws:
            user_ws_task = asyncio.create_task(self.user_ws.connect())
            logger.info("Started User WebSocket for real-time fill notifications")

        # Start ingestion (blocks until stopped)
        try:
            await self.orchestrator.start()
        except asyncio.CancelledError:
            logger.info("Shutting down...")
        finally:
            # Stop user websocket
            if self.user_ws:
                await self.user_ws.disconnect()
            if user_ws_task:
                user_ws_task.cancel()
            # Cancel all orders on shutdown
            logger.info("Cancelling all standing orders...")
            self.executor.cancel_all_orders()
            await self.orchestrator.stop()
            self._print_final_summary()

    def _on_market_update(self, market: MarketState):
        """Called on every market state update from WebSocket."""
        if not market.sync_status:
            return

        if not self.trading_enabled:
            return

        if self.should_stop:
            return

        now_ms = time.time() * 1000
        now = time.time()

        # Start trading timer and initialize ladder on first update
        if self.trading_start_time == 0:
            self.trading_start_time = now
            logger.info(f"Trading started at {datetime.now().strftime('%H:%M:%S')}")
            # Initialize order manager ladder
            if self.order_manager:
                asyncio.create_task(
                    self.order_manager.initialize(market, self.position)
                )

        # Check time limit
        if self.max_seconds:
            elapsed = now - self.trading_start_time
            if elapsed >= self.max_seconds:
                logger.info(f"Reached {self.max_seconds} second time limit. Stopping...")
                self.should_stop = True
                self.trading_enabled = False
                self.executor.cancel_all_orders()
                self._print_session_summary()
                os._exit(0)

        # React to price changes (recalc max prices, cancel/place as needed)
        if self.order_manager:
            asyncio.create_task(
                self.order_manager.on_price_change(market, self.position)
            )

        # Periodic status logging and position sync
        if now_ms - self.last_refresh_ms >= config.REFRESH_INTERVAL_MS:
            self._periodic_check(market, now_ms)
            self.last_refresh_ms = now_ms

    def _periodic_check(self, market: MarketState, now_ms: float):
        """Periodic checks: position sync, circuit breaker, status logging."""
        # Sync position every 15 seconds
        SYNC_INTERVAL_MS = 15000
        if now_ms - self.last_sync_ms >= SYNC_INTERVAL_MS:
            asyncio.create_task(self._sync_position())
            self.last_sync_ms = now_ms

        # Circuit breaker
        total_cost_usd = (self.position.Cy + self.position.Cn) / 1000
        if total_cost_usd >= config.CIRCUIT_BREAKER_USD:
            logger.error(f"[CIRCUIT BREAKER] Total cost ${total_cost_usd:.2f} >= ${config.CIRCUIT_BREAKER_USD}")
            self.executor.cancel_all_orders()
            self.should_stop = True
            return

        # Profit lock - stop trading when guaranteed profit exceeds threshold
        min_qty = min(self.position.Qy, self.position.Qn)
        if min_qty > 0:
            pair_cost_ticks = self.position.get_avg_y_ticks() + self.position.get_avg_n_ticks()
            min_pnl_usd = min_qty * (1000 - pair_cost_ticks) / 1000
            if min_pnl_usd >= config.PROFIT_LOCK_MIN:
                logger.info(f"[PROFIT LOCK] Guaranteed profit ${min_pnl_usd:.2f} >= ${config.PROFIT_LOCK_MIN:.2f} - stopping trading")
                self.executor.cancel_all_orders()
                self.trading_enabled = False
                return

        # Calculate time remaining
        time_remaining_ms = None
        if market.end_timestamp and market.exchange_timestamp:
            time_remaining_ms = market.end_timestamp - market.exchange_timestamp

        # Log status
        self._log_status(market, time_remaining_ms)

    def _log_status(self, market: MarketState, time_remaining_ms: float = None):
        """Log current status."""
        summary = self.executor.get_position_summary()

        # Format time remaining
        if time_remaining_ms:
            mins = int(time_remaining_ms / 60000)
            secs = int((time_remaining_ms % 60000) / 1000)
            time_str = f"{mins}:{secs:02d}"
        else:
            time_str = "??:??"

        # Format prices
        yes_bid = market.get_best_bid_yes()
        yes_ask = market.get_best_ask_yes()
        no_bid = market.get_best_bid_no()
        no_ask = market.get_best_ask_no()

        yes_str = f"${yes_bid/1000:.2f}/{yes_ask/1000:.2f}" if yes_bid and yes_ask else "---"
        no_str = f"${no_bid/1000:.2f}/{no_ask/1000:.2f}" if no_bid and no_ask else "---"

        # Calculate totals
        total_cost_usd = (summary['cost_yes'] + summary['cost_no']) / 1000
        min_pnl = summary.get('min_pnl_usd', 0)

        # Log position - single line
        logger.info(
            f"[{time_str}] "
            f"Y:{summary['qty_yes']:.0f}@${summary['avg_yes']/1000:.2f} "
            f"N:{summary['qty_no']:.0f}@${summary['avg_no']/1000:.2f} | "
            f"Pair:${summary['pair_cost']/1000:.2f} | "
            f"Imbal:{summary['imbalance']:+.0f} | "
            f"Cost:${total_cost_usd:.2f} | "
            f"MinPnL:${min_pnl:+.2f}"
        )

    async def _sync_position(self):
        """Sync position with data-api /positions endpoint."""
        if not self.proxy_wallet or not self.orchestrator:
            return

        market = self.orchestrator.market_state
        url = "https://data-api.polymarket.com/positions"
        params = {
            "user": self.proxy_wallet,
            "market": market.market_id,
            "sizeThreshold": 0
        }

        try:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)

            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        logger.warning(f"[SYNC] API error: {resp.status}")
                        return
                    positions = await resp.json()

            for pos in positions:
                asset = pos.get("asset")
                size = float(pos.get("size", 0))
                cost = float(pos.get("initialValue", 0)) * 1000  # Convert to ticks

                # Only sync UP - if actual > tracked, we missed fills
                # Never sync DOWN - data-api lags ~30s behind MATCHED, so tracked > actual is normal
                if asset == self.executor.token_id_yes:
                    if size > self.position.Qy + 1:
                        logger.warning(f"[SYNC] YES missed fills: tracked={self.position.Qy:.1f} actual={size:.1f}")
                        self.position.Qy = size
                        self.position.Cy = cost
                elif asset == self.executor.token_id_no:
                    if size > self.position.Qn + 1:
                        logger.warning(f"[SYNC] NO missed fills: tracked={self.position.Qn:.1f} actual={size:.1f}")
                        self.position.Qn = size
                        self.position.Cn = cost

        except Exception as e:
            logger.warning(f"[SYNC] Error: {e}")

    def _on_ws_fill(self, fill_event: FillEvent):
        """Called when a fill occurs via User WebSocket (real-time push)."""
        if not self.executor:
            return

        # Update position tracking
        self.executor.handle_ws_fill(fill_event)

        # Update order manager (recalc max prices, cancel/place as needed)
        if self.order_manager and self.trading_enabled:
            side = "yes" if fill_event.asset_id == self.executor.token_id_yes else "no"
            price_ticks = int(fill_event.price * 1000)
            asyncio.create_task(
                self.order_manager.on_fill(
                    side=side,
                    price_ticks=price_ticks,
                    filled_size=fill_event.size,
                    market_state=self.orchestrator.market_state,
                    position_state=self.position,
                    order_id=fill_event.order_id
                )
            )

    def _on_market_switch(self, new_position: PositionState):
        """Called when market switches (every 15 minutes)."""
        self.markets_seen += 1

        # Enable trading after first market switch
        if self.skip_first_market and not self.trading_enabled:
            if self.markets_seen >= 2:
                self.trading_enabled = True
                logger.info("=" * 60)
                logger.info("TRADING NOW ENABLED - First market skipped")
                logger.info("=" * 60)
            else:
                logger.info(f"Skipping market #{self.markets_seen}, waiting for next...")

        # Clear order manager and cancel orders for completed market
        if self.trading_enabled:
            if self.order_manager:
                asyncio.create_task(self.order_manager.on_market_switch())
            elif self.executor:
                self.executor.cancel_all_orders()

            # Log final position if we had any
            if self.position.Qy > 0 or self.position.Qn > 0:
                summary = self.executor.get_position_summary()
                logger.info("=" * 60)
                logger.info(f"MARKET ENDED: {self.orchestrator.current_slug}")
                logger.info(f"  Position: Y:{summary['qty_yes']:.0f} N:{summary['qty_no']:.0f}")
                logger.info(f"  Pair cost: ${summary['pair_cost']/1000:.3f}")
                logger.info(f"  Min P&L: ${summary['min_pnl_usd']:+.2f}")
                logger.info("=" * 60)

                self.markets_traded += 1

            # Check market limit
            if self.max_markets and self.markets_traded >= self.max_markets:
                logger.info(f"Reached {self.max_markets} market(s) limit. Stopping...")
                self.should_stop = True
                self.trading_enabled = False
                self._print_final_summary()
                os._exit(0)

        # Reset for new market
        self.position = new_position
        self.trading_start_time = 0  # Reset so initialize() is called on first update
        if self.executor:
            self.executor.position = new_position
            self.executor.fill_count = 0
            new_market = self.orchestrator.market_state
            self.executor.set_token_ids(new_market.asset_id_yes, new_market.asset_id_no, new_market.market_id)
            if self.user_ws:
                self.user_ws.set_market(new_market.market_id)

        self.market_start_time = time.time()
        self.last_sync_ms = 0
        logger.info(f"NEW MARKET: {self.orchestrator.current_slug}")

    def _print_session_summary(self):
        """Print summary for timed session."""
        if not self.executor:
            return

        summary = self.executor.get_position_summary()
        duration = time.time() - self.trading_start_time if self.trading_start_time else 0

        logger.info("=" * 60)
        logger.info("SESSION SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Duration: {duration:.0f} seconds")
        logger.info(f"Fills: {summary['fill_count']}")
        logger.info(f"YES: {summary['qty_yes']:.1f} @ ${summary['avg_yes']/1000:.3f}")
        logger.info(f"NO: {summary['qty_no']:.1f} @ ${summary['avg_no']/1000:.3f}")
        logger.info(f"Pair cost: ${summary['pair_cost']/1000:.3f}")
        logger.info(f"Min P&L: ${summary['min_pnl_usd']:+.2f}")
        logger.info("=" * 60)

    def _print_final_summary(self):
        """Print final summary on exit."""
        logger.info("=" * 60)
        logger.info("LIVE TRADING COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Markets traded: {self.markets_traded}")
        logger.info("=" * 60)


async def main(max_markets: int = None, max_seconds: int = None):
    # Get private key from environment
    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY")
    if not private_key:
        logger.error("POLYMARKET_PRIVATE_KEY environment variable not set!")
        logger.error("Export your wallet private key:")
        logger.error("  export POLYMARKET_PRIVATE_KEY='0x...'")
        return

    # Get proxy wallet (optional - for Polymarket embedded wallet users)
    proxy_wallet = os.environ.get("POLYMARKET_PROXY_WALLET", "")

    # Confirm before starting
    print("\n" + "=" * 60)
    print("WARNING: LIVE TRADING MODE")
    print("=" * 60)
    print(f"Circuit breaker: ${config.CIRCUIT_BREAKER_USD:.2f}")
    if max_markets:
        print(f"Will stop after {max_markets} market(s).")
    if max_seconds:
        print(f"Will stop after {max_seconds} seconds of trading.")
    if proxy_wallet:
        print(f"Using proxy wallet: {proxy_wallet}")
    print(f"This will submit REAL orders using your wallet.")
    print("=" * 60)
    confirm = input("Type 'YES' to confirm: ")
    if confirm != "YES":
        print("Aborted.")
        return

    trader = LiveTrader(private_key, proxy_wallet, max_markets=max_markets, max_seconds=max_seconds)
    await trader.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live trading for Polymarket")
    parser.add_argument(
        "-n", "--markets",
        type=int,
        default=None,
        help="Number of markets to trade before exiting (default: unlimited)"
    )
    parser.add_argument(
        "-s", "--seconds",
        type=int,
        default=None,
        help="Stop after this many seconds of trading (default: unlimited)"
    )
    args = parser.parse_args()

    try:
        asyncio.run(main(max_markets=args.markets, max_seconds=args.seconds))
    except KeyboardInterrupt:
        print("\nInterrupted by user")
