"""
Simulated order execution with latency and partial fill simulation.
"""
import asyncio
import logging
import time
from typing import Dict, Optional, Callable
from execution.order_state import OrderState, OrderStatus
from strategy.signals import TradeSignal
from state.market_state import MarketState
import config

logger = logging.getLogger(__name__)


class SimulatedExecutor:
    """
    Simulates order execution with latency and partial fills.
    
    Features:
    - Simulates network latency (config.LATENCY_MS)
    - Simulates partial fills based on market depth
    - Schedules fill events over time
    """
    
    def __init__(
        self,
        on_fill: Optional[Callable[[str, float, float], None]] = None
    ):
        """
        Initialize simulated executor.
        
        Args:
            on_fill: Callback when a fill occurs: (order_id, filled_size, fill_price)
        """
        self.on_fill = on_fill
        self.pending_orders: Dict[str, OrderState] = {}
        self.fill_tasks: Dict[str, asyncio.Task] = {}
    
    async def submit_order(
        self,
        signal: TradeSignal,
        market_state: MarketState,
        order_id: str,
        order: OrderState
    ):
        """
        Simulate order submission.
        
        Args:
            signal: Trade signal to execute
            market_state: Current market state
            order_id: Unique order identifier
            order: Pre-created OrderState object (from execution engine)
        """
        # Use the provided order state (already tracked in execution engine)
        self.pending_orders[order_id] = order
        
        # Simulate network latency before order is "submitted"
        await asyncio.sleep(config.LATENCY_MS / 1000.0)
        
        # Schedule fills based on market depth
        await self._schedule_fills(order, market_state)
        
        return order
    
    async def _schedule_fills(
        self,
        order: OrderState,
        market_state: MarketState
    ):
        """
        Schedule fill events based on market depth.
        
        Strategy:
        1. Check available size at order price
        2. If available < order.size: simulate partial fills
        3. If available >= order.size: full fill after latency
        """
        # Get available size at order price
        if order.side == "YES":
            available_size = self._get_available_size_at_price(
                market_state.order_book_yes_asks,
                order.price
            )
        else:  # NO
            available_size = self._get_available_size_at_price(
                market_state.order_book_no_asks,
                order.price
            )
        
        if available_size <= 0:
            # No liquidity at this price - order stays pending
            logger.debug(f"Order {order.order_id}: No liquidity at {order.price} ticks")
            return
        
        # Determine fill strategy
        if available_size >= order.size:
            # Full fill available - fill after latency
            await asyncio.sleep(config.LATENCY_MS / 1000.0)
            await self._execute_fill(order.order_id, order.size, order.price)
        else:
            # Partial fill - simulate multiple fills
            # First fill: 30% of available or 30% of order size, whichever is smaller
            first_fill_size = min(available_size * 0.3, order.size * 0.3)
            remaining_size = min(available_size - first_fill_size, order.size - first_fill_size)
            
            # First partial fill after 200ms
            await asyncio.sleep(0.2)
            await self._execute_fill(order.order_id, first_fill_size, order.price)
            
            # Second fill (remaining) after additional 300ms (500ms total)
            if remaining_size > 0:
                await asyncio.sleep(0.3)
                await self._execute_fill(order.order_id, remaining_size, order.price)
    
    def _get_available_size_at_price(self, order_book, price: float) -> float:
        """
        Get total available size at or better than given price.
        
        Args:
            order_book: SortedDict of price -> size
            price: Price limit in ticks
        
        Returns:
            Total available size
        """
        total_size = 0.0
        for book_price, size in order_book.items():
            if book_price <= price:
                total_size += size
            else:
                break  # Prices are sorted, can stop here
        return total_size
    
    async def _execute_fill(
        self,
        order_id: str,
        fill_size: float,
        fill_price: float
    ):
        """
        Execute a fill for an order.
        
        Args:
            order_id: Order identifier
            fill_size: Size to fill
            fill_price: Fill price in ticks
        """
        if order_id not in self.pending_orders:
            logger.warning(f"Fill for unknown order: {order_id}")
            return
        
        order = self.pending_orders[order_id]
        
        # Don't overfill
        remaining = order.get_remaining_size()
        actual_fill_size = min(fill_size, remaining)
        
        if actual_fill_size <= 0:
            return  # Already filled
        
        # Add fill to order
        order.add_fill(actual_fill_size, fill_price)
        
        logger.info(f"✅ Fill: Order {order_id} - {actual_fill_size:.2f} shares @ {fill_price:.1f} ticks "
                   f"({order.filled_size:.2f}/{order.size:.2f} filled)")
        
        # Notify callback
        if self.on_fill:
            self.on_fill(order_id, actual_fill_size, fill_price)
    
    def get_order(self, order_id: str) -> Optional[OrderState]:
        """Get order state by ID."""
        return self.pending_orders.get(order_id)
    
    def get_pending_orders(self) -> Dict[str, OrderState]:
        """Get all pending orders."""
        return {oid: order for oid, order in self.pending_orders.items() 
                if not order.is_complete()}
    
    def cancel_order(self, order_id: str):
        """Cancel an order."""
        if order_id in self.pending_orders:
            self.pending_orders[order_id].cancel()
            # Cancel any pending fill tasks
            if order_id in self.fill_tasks:
                self.fill_tasks[order_id].cancel()
                del self.fill_tasks[order_id]

