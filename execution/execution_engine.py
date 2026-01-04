"""
Execution engine - orchestrates order lifecycle and position updates.
"""
import asyncio
import logging
import uuid
from typing import Dict, Optional, Callable
from execution.order_state import OrderState, OrderStatus
from execution.simulator import SimulatedExecutor
from execution.backtest_executor import BacktestExecutor
from execution.polymarket_api import PolymarketAPIClient
from strategy.signals import TradeSignal
from state.market_state import MarketState
from state.position_state import PositionState

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """
    Main execution engine that manages order lifecycle and position updates.
    
    Supports both simulated and real execution modes.
    """
    
    def __init__(
        self,
        mode: str = "simulated",
        market_state: Optional[MarketState] = None,
        position_state: Optional[PositionState] = None,
        on_order_update: Optional[Callable[[OrderState], None]] = None
    ):
        """
        Initialize execution engine.
        
        Args:
            mode: "simulated" or "real"
            market_state: Market state (needed for simulated execution)
            position_state: Position state to update on fills
            on_order_update: Optional callback when order state changes
        """
        self.mode = mode
        self.market_state = market_state
        self.position_state = position_state
        self.on_order_update = on_order_update
        
        # Initialize executor based on mode
        if mode == "simulated":
            self.executor = SimulatedExecutor(on_fill=self._on_fill)
            self.api_client = None
        elif mode == "backtest":
            self.executor = BacktestExecutor(on_fill=self._on_fill)
            self.api_client = None
        elif mode == "real":
            self.api_client = PolymarketAPIClient()
            self.executor = None
        else:
            raise ValueError(f"Unknown execution mode: {mode}")
        
        # Track all orders
        self.orders: Dict[str, OrderState] = {}
        self.order_counter = 0
    
    def set_market_state(self, market_state: MarketState):
        """Update market state (needed for simulated execution)."""
        self.market_state = market_state
        if self.executor:
            # Simulated executor needs market state for fill simulation
            pass  # Market state is passed per order
    
    def set_position_state(self, position_state: PositionState):
        """Update position state."""
        self.position_state = position_state
    
    async def execute_signal(
        self,
        signal: TradeSignal,
        market_state: Optional[MarketState] = None
    ) -> OrderState:
        """Execute a trade signal (async for simulated/real, sync for backtest)."""
        """
        Execute a trade signal.
        
        Args:
            signal: Trade signal to execute
            market_state: Optional market state (uses instance state if None)
        
        Returns:
            OrderState object
        """
        # Use provided market state or instance state
        market = market_state or self.market_state
        if not market:
            raise ValueError("Market state required for execution")
        
        # Generate order ID
        order_id = f"order_{self.order_counter}_{uuid.uuid4().hex[:8]}"
        self.order_counter += 1
        
        logger.info(f"📤 Executing signal: {signal.side} @ {signal.price:.1f} ticks, size={signal.size:.1f}, "
                   f"priority={signal.priority}, reason={signal.reason}")
        
        # Track order FIRST (before execution) so fills can find it
        # Create order state first
        order = OrderState(
            order_id=order_id,
            side=signal.side,
            price=signal.price,
            size=signal.size,
            status=OrderStatus.PENDING
        )
        self.orders[order_id] = order
        logger.debug(f"Order {order_id} added to tracking (total orders: {len(self.orders)})")
        
        # Ensure position state is set
        if not self.position_state:
            logger.warning("ExecutionEngine has no position_state - fills won't update position!")
        
        # Update pending flags in position state (before execution starts)
        if self.position_state:
            if signal.side == "YES":
                self.position_state.pending_yes = True
            else:
                self.position_state.pending_no = True
        
        # Execute based on mode
        if self.mode == "simulated":
            await self.executor.submit_order(signal, market, order_id, order)
        elif self.mode == "backtest":
            self.executor.submit_order(signal, market, order_id, order)
        else:  # real
            # Convert signal to API order format
            order = await self._submit_real_order(signal, order_id)
            self.orders[order_id] = order
        
        # Notify callback
        if self.on_order_update:
            self.on_order_update(order)
        
        return order
    
    async def _submit_real_order(self, signal: TradeSignal, order_id: str) -> OrderState:
        """Submit order to real Polymarket API."""
        # Get asset ID from market state
        if not self.market_state:
            raise ValueError("Market state required for real order submission")
        
        asset_id = (self.market_state.asset_id_yes if signal.side == "YES" 
                   else self.market_state.asset_id_no)
        
        # Convert price from ticks to decimal
        price_decimal = signal.price / 1000.0
        
        # Submit via API
        api_order_id = await self.api_client.submit_order(
            asset_id=asset_id,
            side="BUY",
            price=price_decimal,
            size=signal.size
        )
        
        # Create order state
        order = OrderState(
            order_id=order_id,
            side=signal.side,
            price=signal.price,
            size=signal.size,
            status=OrderStatus.PENDING
        )
        
        # Store API order ID mapping (for status checks)
        order.api_order_id = api_order_id
        
        return order
    
    def _on_fill(self, order_id: str, filled_size: float, fill_price: float):
        """
        Handle fill event from executor.
        
        Args:
            order_id: Order identifier
            filled_size: Size filled
            fill_price: Fill price in ticks
        """
        if order_id not in self.orders:
            logger.warning(f"Fill for unknown order: {order_id} (available orders: {list(self.orders.keys())[:5]})")
            return
        
        order = self.orders[order_id]
        
        # Note: order.add_fill() was already called in simulator, so order status is updated
        # We just need to update position state here
        
        # Update position state
        if self.position_state:
            if order.side == "YES":
                self.position_state.Qy += filled_size
                self.position_state.Cy += fill_price * filled_size
                logger.debug(f"Position updated: Qy={self.position_state.Qy:.2f}, Cy={self.position_state.Cy:.2f}")
                # Clear pending flag if fully filled
                if order.status == OrderStatus.FILLED:
                    self.position_state.pending_yes = False
            else:  # NO
                self.position_state.Qn += filled_size
                self.position_state.Cn += fill_price * filled_size
                logger.debug(f"Position updated: Qn={self.position_state.Qn:.2f}, Cn={self.position_state.Cn:.2f}")
                # Clear pending flag if fully filled
                if order.status == OrderStatus.FILLED:
                    self.position_state.pending_no = False
        
        # Notify callback
        if self.on_order_update:
            self.on_order_update(order)
    
    def get_order(self, order_id: str) -> Optional[OrderState]:
        """Get order state by ID."""
        return self.orders.get(order_id)
    
    def get_pending_orders(self) -> Dict[str, OrderState]:
        """Get all pending orders."""
        if self.mode == "simulated":
            return self.executor.get_pending_orders()
        else:
            # For real mode, filter by status
            return {oid: order for oid, order in self.orders.items() 
                   if order.status == OrderStatus.PENDING or 
                      order.status == OrderStatus.PARTIALLY_FILLED}
    
    async def cancel_order(self, order_id: str):
        """Cancel an order."""
        if order_id not in self.orders:
            logger.warning(f"Cannot cancel unknown order: {order_id}")
            return
        
        if self.mode == "simulated":
            self.executor.cancel_order(order_id)
        else:
            # Cancel via API
            order = self.orders[order_id]
            if hasattr(order, 'api_order_id'):
                await self.api_client.cancel_order(order.api_order_id)
        
        self.orders[order_id].cancel()
        
        # Clear pending flags
        if self.position_state:
            order = self.orders[order_id]
            if order.side == "YES":
                self.position_state.pending_yes = False
            else:
                self.position_state.pending_no = False
        
        # Notify callback
        if self.on_order_update:
            self.on_order_update(self.orders[order_id])

