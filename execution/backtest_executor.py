"""
Simple backtest executor - fills orders immediately.
"""
import logging
from typing import Dict, Optional, Callable
from execution.order_state import OrderState, OrderStatus
from strategy.signals import TradeSignal
from state.market_state import MarketState

logger = logging.getLogger(__name__)


class BacktestExecutor:
    """
    Simple executor for backtesting.
    Fills orders immediately if liquidity exists.
    """
    
    def __init__(
        self,
        on_fill: Optional[Callable[[str, float, float], None]] = None
    ):
        """
        Initialize backtest executor.
        
        Args:
            on_fill: Callback when a fill occurs: (order_id, filled_size, fill_price)
        """
        self.on_fill = on_fill
        self.pending_orders: Dict[str, OrderState] = {}
    
    def submit_order(
        self,
        signal: TradeSignal,
        market_state: MarketState,
        order_id: str,
        order: OrderState
    ):
        """
        Submit order and fill immediately if liquidity exists.
        
        Args:
            signal: Trade signal to execute
            market_state: Current market state
            order_id: Unique order identifier
            order: Pre-created OrderState object
        """
        self.pending_orders[order_id] = order
        
        # Check if liquidity exists at order price
        if signal.side == "YES":
            best_ask = market_state.get_best_ask_yes()
            available_size = market_state.get_best_ask_size_yes() if best_ask else 0.0
            fill_price = best_ask if best_ask else None
        else:  # NO
            best_ask = market_state.get_best_ask_no()
            available_size = market_state.get_best_ask_size_no() if best_ask else 0.0
            fill_price = best_ask if best_ask else None
        
        # Fill if price is acceptable (best ask <= our limit price) and liquidity exists
        if fill_price is not None and fill_price <= signal.price:
            if available_size >= signal.size:
                # Full fill
                self._execute_fill(order_id, signal.size, fill_price)
            elif available_size > 0:
                # Partial fill
                self._execute_fill(order_id, available_size, fill_price)
    
    def _execute_fill(
        self,
        order_id: str,
        fill_size: float,
        fill_price: float
    ):
        """Execute a fill for an order."""
        if order_id not in self.pending_orders:
            return
        
        order = self.pending_orders[order_id]
        
        # Don't overfill
        remaining = order.get_remaining_size()
        actual_fill_size = min(fill_size, remaining)
        
        if actual_fill_size <= 0:
            return
        
        # Add fill to order
        order.add_fill(actual_fill_size, fill_price)
        
        # Notify callback
        if self.on_fill:
            self.on_fill(order_id, actual_fill_size, fill_price)
    
    def get_pending_orders(self) -> Dict[str, OrderState]:
        """Get all pending orders."""
        return {oid: order for oid, order in self.pending_orders.items() 
                if not order.is_complete()}

