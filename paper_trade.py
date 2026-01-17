#!/usr/bin/env python3
"""
Live Paper Trading for Polymarket.

Connects to live market data and simulates trading without real orders.
Use this to validate the strategy before going live.

Usage:
    python paper_trade.py              # Run indefinitely
    python paper_trade.py --markets 1  # Run for 1 market then exit
    python paper_trade.py -n 3         # Run for 3 markets then exit
    python paper_trade.py --seconds 60 # Run for 60 seconds then exit
    python paper_trade.py -s 120       # Run for 2 minutes then exit
"""
import argparse
import asyncio
import logging
import time
from datetime import datetime

from ingestion.orchestrator import IngestionOrchestrator
from state.market_state import MarketState
from state.position_state import PositionState
from strategy.continuous_arb import calculate_target_orders
from execution.paper_executor import PaperExecutor, PaperFill, TradeLogger
import config

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Reduce noise from libraries
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class PaperTrader:
    """
    Paper trading bot that runs against live market data.
    """

    def __init__(self, max_markets: int = None, skip_first: bool = True, max_seconds: int = None):
        self.orchestrator: IngestionOrchestrator = None
        self.executor: PaperExecutor = None
        self.position: PositionState = None
        self.trade_logger: TradeLogger = None

        # Timing
        self.last_refresh_ms = 0
        self.market_start_time = 0
        self.trading_start_time = 0  # When trading actually started

        # Stats per market
        self.markets_traded = 0
        self.total_pnl = 0.0

        # Skip first market - only trade after a market switch
        self.skip_first_market = skip_first
        self.trading_enabled = not skip_first  # If not skipping, enable immediately
        self.markets_seen = 0 if skip_first else 1  # Track how many markets we've seen

        # Market limit
        self.max_markets = max_markets
        self.max_seconds = max_seconds  # Auto-stop after this many seconds
        self.should_stop = False

    async def start(self):
        """Start paper trading."""
        logger.info("=" * 60)
        logger.info("PAPER TRADING MODE")
        logger.info("=" * 60)
        logger.info("Connecting to live Polymarket data...")
        logger.info("No real orders will be placed.")
        if self.max_markets:
            logger.info(f"Will stop after {self.max_markets} market(s).")
        if self.max_seconds:
            logger.info(f"Will stop after {self.max_seconds} seconds of trading.")
        if not self.skip_first_market:
            logger.info("Trading IMMEDIATELY (not skipping first market)")
        logger.info("=" * 60)

        # Create orchestrator with callbacks
        self.orchestrator = IngestionOrchestrator(
            on_market_state_update=self._on_market_update,
            on_position_state_reset=self._on_market_switch
        )

        # Initialize (fetches current market)
        await self.orchestrator.initialize()

        # Create trade logger
        self.trade_logger = TradeLogger()
        logger.info(f"Trade log: {self.trade_logger.log_file}")

        # Set up position and executor
        self.position = self.orchestrator.position_state
        self.executor = PaperExecutor(
            position=self.position,
            on_fill=self._on_fill,
            trade_logger=self.trade_logger
        )

        # Set initial market ID
        self.executor.set_market_id(self.orchestrator.market_state.market_id)

        self.market_start_time = time.time()
        logger.info(f"Market: {self.orchestrator.market_state.slug}")
        logger.info(f"Strike: ${self.orchestrator.market_state.strike_price:,.0f}")
        if self.skip_first_market:
            logger.info("⏳ WAITING for next market (skipping first market)...")
        else:
            logger.info("Waiting for orderbook sync...")

        # Start ingestion (blocks until stopped)
        try:
            await self.orchestrator.start()
        except KeyboardInterrupt:
            logger.info("Shutting down...")
        finally:
            await self.orchestrator.stop()
            self._print_final_summary()

    def _on_market_update(self, market: MarketState):
        """Called on every market state update from WebSocket."""
        # Skip if not synced
        if not market.sync_status:
            return

        # Skip if trading not enabled (waiting for first market switch)
        if not self.trading_enabled:
            return

        # Check if we should stop
        if self.should_stop:
            return

        now_ms = time.time() * 1000
        now = time.time()

        # Start trading timer on first update
        if self.trading_start_time == 0:
            self.trading_start_time = now
            logger.info(f"Trading started at {datetime.now().strftime('%H:%M:%S')}")

        # Check duration limit
        if self.max_seconds and (now - self.trading_start_time) >= self.max_seconds:
            logger.info(f"Reached {self.max_seconds} second time limit. Stopping...")
            self.should_stop = True
            self._print_session_summary()
            asyncio.create_task(self._shutdown())
            return

        # Check fills on standing orders
        if self.executor:
            self.executor.check_fills(market)

        # Refresh orders every REFRESH_INTERVAL_MS
        if now_ms - self.last_refresh_ms >= config.REFRESH_INTERVAL_MS:
            self._refresh_orders(market)
            self.last_refresh_ms = now_ms

    def _refresh_orders(self, market: MarketState):
        """Cancel old orders and place new ones."""
        # Check for fills one more time before canceling to avoid missing any
        self.executor.check_fills(market)

        # Cancel all standing orders
        self.executor.cancel_all_orders()

        # Calculate time remaining
        time_remaining_ms = None
        if market.end_timestamp and market.exchange_timestamp:
            time_remaining_ms = market.end_timestamp - market.exchange_timestamp

        # Get new orders from strategy
        orders = calculate_target_orders(
            position=self.position,
            market=market,
            time_remaining_ms=time_remaining_ms
        )

        # Place new orders
        if orders:
            self.executor.place_orders(orders)

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

    def _on_fill(self, fill: PaperFill):
        """Called when a paper fill occurs."""
        # Already logged in executor, just update stats if needed
        pass

    def _on_market_switch(self, new_position: PositionState):
        """Called when market switches (every 15 minutes)."""
        self.markets_seen += 1

        # Enable trading after seeing second market (first real switch)
        if self.skip_first_market and not self.trading_enabled:
            if self.markets_seen >= 2:
                self.trading_enabled = True
                logger.info("=" * 60)
                logger.info("TRADING NOW ENABLED - First market skipped")
                logger.info("=" * 60)
            else:
                logger.info(f"Skipping market #{self.markets_seen}, waiting for next...")
                # Reset position and return early - don't calculate P&L for skipped market
                self.position = new_position
                if self.executor:
                    self.executor.position = new_position
                    self.executor.standing_orders.clear()
                    self.executor.fills.clear()
                return

        if self.executor and self.position:
            # Calculate P&L for old market using cached previous_market_info
            # This is critical: market_state is already updated to the NEW market by the time
            # this callback fires, so we MUST use the cached values
            prev_info = self.orchestrator.previous_market_info

            if prev_info and prev_info.get("start_btc_price", 0) > 0 and (self.position.Qy > 0 or self.position.Qn > 0):
                start_price = prev_info["start_btc_price"]  # BTC price at market start
                end_price = prev_info["end_btc_price"]      # BTC price at market end
                old_slug = prev_info["slug"]
                old_market_id = prev_info["market_id"]

                # Polymarket resolution: "Up" wins if end_price >= start_price
                # Note: Uses >= not > (per Polymarket docs)
                yes_wins = end_price >= start_price
                pnl_info = self.executor.calculate_pnl(yes_wins)

                # Log to trade logger
                self.executor.log_market_resolution(old_market_id, yes_wins)

                logger.info("=" * 60)
                logger.info(f"MARKET RESOLVED: {old_slug}")
                logger.info(f"  BTC: ${end_price:,.0f} (end) vs ${start_price:,.0f} (start)")
                logger.info(f"  Winner: {pnl_info['winner']}")
                logger.info(f"  Position: {pnl_info['winning_shares']:.0f} winning, {pnl_info['losing_shares']:.0f} losing")
                logger.info(f"  Cost: ${pnl_info['total_cost_usd']:.2f}")
                logger.info(f"  Payout: ${pnl_info['payout_usd']:.2f}")
                logger.info(f"  P&L: ${pnl_info['pnl_usd']:.2f} ({pnl_info['roi_percent']:.1f}%)")
                logger.info("=" * 60)

                self.total_pnl += pnl_info['pnl_usd']
                self.markets_traded += 1

                # Check if we've hit the market limit
                if self.max_markets and self.markets_traded >= self.max_markets:
                    logger.info(f"Reached {self.max_markets} market(s) limit. Stopping...")
                    self.should_stop = True
                    # Trigger graceful shutdown
                    asyncio.create_task(self._shutdown())
                    return

        # Reset executor for new market
        self.position = new_position
        if self.executor:
            self.executor.position = new_position
            self.executor.standing_orders.clear()
            self.executor.fills.clear()
            # Set new market ID for logging
            if hasattr(self.orchestrator, 'current_market_id'):
                self.executor.set_market_id(self.orchestrator.current_market_id)

        self.market_start_time = time.time()
        logger.info(f"NEW MARKET: {self.orchestrator.current_slug}")

    async def _shutdown(self):
        """Gracefully shutdown after market limit reached."""
        self._print_final_summary()
        await self.orchestrator.stop()
        # Exit the event loop
        loop = asyncio.get_event_loop()
        loop.stop()

    def _print_session_summary(self):
        """Print summary for timed session (before market resolves)."""
        if not self.executor:
            return

        summary = self.executor.get_position_summary()
        duration = time.time() - self.trading_start_time if self.trading_start_time else 0

        logger.info("=" * 60)
        logger.info("TIMED SESSION SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Duration: {duration:.0f} seconds")
        logger.info(f"Total fills: {summary['total_fills']}")
        logger.info(f"YES: {summary['qty_yes']:.1f} shares @ ${summary['avg_yes']/1000:.4f} avg")
        logger.info(f"NO: {summary['qty_no']:.1f} shares @ ${summary['avg_no']/1000:.4f} avg")
        logger.info(f"PAIR COST: ${summary['pair_cost']/1000:.4f}")
        logger.info(f"Total cost: ${(summary['cost_yes'] + summary['cost_no'])/1000:.2f}")
        logger.info(f"Imbalance: {summary['imbalance']:+.1f}")
        logger.info(f"Min P&L: ${summary['min_pnl_usd']:+.2f}")
        logger.info("=" * 60)

        # Save to trade logger
        if self.trade_logger:
            self.trade_logger.save_summary(summary['min_pnl_usd'], 0)

    def _print_final_summary(self):
        """Print final summary on exit."""
        # Save trade log
        if self.trade_logger and self.markets_traded > 0:
            self.trade_logger.save_summary(self.total_pnl, self.markets_traded)

        logger.info("=" * 60)
        logger.info("PAPER TRADING SESSION COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Markets traded: {self.markets_traded}")
        logger.info(f"Total P&L: ${self.total_pnl:.2f}")
        if self.markets_traded > 0:
            logger.info(f"Avg P&L per market: ${self.total_pnl / self.markets_traded:.2f}")
        logger.info("=" * 60)


async def main(max_markets: int = None, skip_first: bool = True, max_seconds: int = None):
    trader = PaperTrader(max_markets=max_markets, skip_first=skip_first, max_seconds=max_seconds)
    await trader.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Paper trading for Polymarket")
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
    parser.add_argument(
        "--no-skip",
        action="store_true",
        help="Start trading immediately (don't skip first partial market)"
    )
    args = parser.parse_args()

    asyncio.run(main(max_markets=args.markets, skip_first=not args.no_skip, max_seconds=args.seconds))
