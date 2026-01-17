"""
Order Manager - Orchestrates Triple Gate order management.

Triple Gate Pricing:
- P_acct (Accountant): What can I afford? (position-aware max price)
- P_mkt (Market Maker): What does the market say? (replacement cost + skew)
- Cap_exec (Execution): Maker or Taker? (spread crossing control)
- Final price = min(P_acct, P_mkt, Cap_exec)

Reconciliation:
- Phase 1: Cancel stale orders (not in ideal ladder)
- Phase 2: Place/Stack/Shrink/Hold for each ideal rung
"""
import asyncio
import logging
from math import floor

from execution.order_tracker import OrderTracker
from state.market_state import MarketState
from state.position_state import PositionState
import config

logger = logging.getLogger(__name__)


class OrderManager:
    """
    Orchestrates order placement using Triple Gate pricing and diff-based reconciliation.

    On every event (fill, price change):
    1. Calculate ideal ladder using Triple Gate pricing
    2. Compare to current orders
    3. Cancel stale, Place new, Stack more, Shrink oversized, or Hold
    """

    def __init__(self, executor):
        """
        Initialize OrderManager.

        Args:
            executor: RealExecutor instance for placing/cancelling orders
        """
        self.executor = executor
        self.tracker = OrderTracker()

        # Track if we've initialized
        self._initialized = False

        # Lock to serialize fill processing (prevents race conditions)
        self._fill_lock = asyncio.Lock()

    # =========================================================================
    # INITIALIZATION
    # =========================================================================

    async def initialize(self, market_state: MarketState, position_state: PositionState):
        """
        Initialize ladders on market start.
        Called once when trading begins on a new market.
        """
        logger.info("[ORDER_MGR] Initializing ladders...")

        # Reconcile will place full ladder since tracker is empty
        await self._reconcile_orders("yes", market_state, position_state)
        await self._reconcile_orders("no", market_state, position_state)

        self._initialized = True

        summary = self.tracker.summary()
        logger.info(
            f"[ORDER_MGR] Initialized: YES {summary['yes_count']} orders "
            f"({summary['yes_range'][0]/10:.0f}c-{summary['yes_range'][1]/10:.0f}c), "
            f"NO {summary['no_count']} orders "
            f"({summary['no_range'][0]/10:.0f}c-{summary['no_range'][1]/10:.0f}c)"
        )

    # =========================================================================
    # EVENT HANDLERS
    # =========================================================================

    async def on_fill(
        self,
        side: str,
        price_ticks: int,
        filled_size: float,
        market_state: MarketState,
        position_state: PositionState,
        order_id: str
    ):
        """
        Handle a fill event.
        1. Update tracker
        2. Reconcile both sides (position changed, may need to adjust)
        """
        async with self._fill_lock:
            side_lower = side.lower()

            # Update tracker
            self.tracker.update_fill(side_lower, price_ticks, filled_size, order_id)

            # Reconcile both sides (position changed affects both)
            await self._reconcile_orders("yes", market_state, position_state)
            await self._reconcile_orders("no", market_state, position_state)

            # Log state after fill
            summary = self.tracker.summary()
            logger.info(
                f"[ORDER_MGR] After fill: Standing YES={summary['yes_count']} NO={summary['no_count']}"
            )

    async def on_price_change(
        self,
        market_state: MarketState,
        position_state: PositionState
    ):
        """
        Handle best bid/ask change.
        Reconcile recalculates ideal ladder and only makes API calls if needed.
        """
        if not self._initialized:
            return

        await self._reconcile_orders("yes", market_state, position_state)
        await self._reconcile_orders("no", market_state, position_state)

    async def on_market_switch(self):
        """
        Handle market switch.
        Clear all tracking and cancel all orders.
        """
        logger.info("[ORDER_MGR] Market switch - clearing all orders")
        self.tracker.clear_all()
        self.executor.cancel_all_orders()
        self._initialized = False

    # =========================================================================
    # TRIPLE GATE PRICING
    # =========================================================================

    def _get_net_position(self, side: str, position_state: PositionState) -> int:
        """
        Get net position for a side.
        Positive = heavy on this side, Negative = light on this side.
        """
        if side == "yes":
            return position_state.Qy - position_state.Qn
        else:
            return position_state.Qn - position_state.Qy

    def _calc_p_acct(self, side: str, position_state: PositionState) -> float:
        """
        Calculate Accountant price (P_acct) - "What can I afford?"
        Ensures we never lock in a portfolio loss.
        """
        net_pos = self._get_net_position(side, position_state)

        if net_pos < 0:  # LIGHT - need to buy, use position-aware formula
            # Example: 30 YES @ 40c, 130 NO @ 45c → need 100 YES to balance
            # max_yes = (130 × (100c - 45c) - cost_yes) / 100
            if side == "yes":
                heavy_qty = position_state.Qn
                heavy_avg = position_state.get_avg_n_ticks() or 0
                light_cost = position_state.Cy
            else:
                heavy_qty = position_state.Qy
                heavy_avg = position_state.get_avg_y_ticks() or 0
                light_cost = position_state.Cn

            shares_needed = abs(net_pos)
            if shares_needed == 0 or heavy_qty == 0:
                return 990  # Max valid price

            p_acct = (heavy_qty * (1000 - heavy_avg) - light_cost) / shares_needed

        else:  # HEAVY/NEUTRAL - use conservative formula
            if side == "yes":
                avg_opp = position_state.get_avg_n_ticks() or 0
            else:
                avg_opp = position_state.get_avg_y_ticks() or 0

            p_acct = 1000 - avg_opp - config.BASE_MARGIN_TICKS

        return p_acct

    def _calc_p_mkt(self, side: str, market_state: MarketState, position_state: PositionState) -> float:
        """
        Calculate Market price (P_mkt) - "What does the market say?"
        Based on replacement cost with inventory skew.
        """
        # Anchor = replacement cost
        if side == "yes":
            ask_opp = market_state.best_ask_no or 1000
        else:
            ask_opp = market_state.best_ask_yes or 1000

        anchor = 1000 - ask_opp - config.BASE_MARGIN_TICKS

        # Inventory skew: heavy → lower bid, light → higher bid
        net_pos = self._get_net_position(side, position_state)
        raw_skew = net_pos * config.GAMMA * 1000  # Convert to ticks
        skew = max(-config.MAX_SKEW_TICKS, min(config.MAX_SKEW_TICKS, raw_skew))

        return anchor - skew

    def _calc_cap_exec(self, side: str, market_state: MarketState, position_state: PositionState) -> float:
        """
        Calculate Execution cap (Cap_exec) - "Maker or Taker?"
        Controls spread crossing.
        """
        if side == "yes":
            ask_this = market_state.best_ask_yes or 1000
        else:
            ask_this = market_state.best_ask_no or 1000

        net_pos = self._get_net_position(side, position_state)

        if net_pos < 0:  # LIGHT - can cross spread
            return ask_this + config.SLIPPAGE_TOL_TICKS
        else:  # HEAVY/NEUTRAL - must be maker
            return ask_this - config.TICK_SIZE

    def _calc_final_price(self, side: str, market_state: MarketState, position_state: PositionState) -> int:
        """
        Triple Gate: final price is min(P_acct, P_mkt, Cap_exec).
        """
        p_acct = self._calc_p_acct(side, position_state)
        p_mkt = self._calc_p_mkt(side, market_state, position_state)
        cap_exec = self._calc_cap_exec(side, market_state, position_state)

        p_final = min(p_acct, p_mkt, cap_exec)

        # Clamp to valid range
        return int(max(config.MIN_PRICE, min(990, p_final)))

    # =========================================================================
    # SIZING
    # =========================================================================

    def _calc_target_size(self, side: str, position_state: PositionState) -> float:
        """
        Calculate target order size based on inventory "hunger".
        Neutral = BASE_SIZE, Heavy = 0, Light = 2x BASE_SIZE.
        """
        net_pos = self._get_net_position(side, position_state)

        # Hard stop at MAX_POSITION
        if net_pos >= config.MAX_POSITION:
            return 0.0

        # Linear scaling: scalar = 1.0 at neutral, 0.0 at +MAX, 2.0 at -MAX
        scalar = 1.0 - (net_pos / config.MAX_POSITION)
        scalar = max(0.0, min(2.0, scalar))

        # Round down to 2 decimal places (Polymarket precision)
        return floor(config.BASE_SIZE * scalar * 100) / 100

    # =========================================================================
    # LADDER CONSTRUCTION
    # =========================================================================

    def _build_ideal_ladder(self, p_final: int, target_size: float) -> dict[int, float]:
        """
        Build ideal ladder from p_final DOWN.
        Returns {price_ticks: size} for each rung.
        """
        if target_size <= 0:
            return {}

        ladder = {}
        for i in range(config.LADDER_DEPTH):
            price = p_final - (i * config.TICK_SIZE)  # 1c spacing
            if price >= config.MIN_PRICE:
                ladder[price] = target_size
        return ladder

    # =========================================================================
    # DIFF ENGINE (RECONCILIATION)
    # =========================================================================

    async def _reconcile_orders(
        self,
        side: str,
        market_state: MarketState,
        position_state: PositionState
    ):
        """
        Reconcile current orders with ideal ladder.
        Phase 1: Cancel stale orders (not in ideal ladder)
        Phase 2: Place/Stack/Shrink/Hold for each ideal rung
        """
        # Calculate ideal state
        p_final = self._calc_final_price(side, market_state, position_state)
        target_size = self._calc_target_size(side, position_state)
        ideal_ladder = self._build_ideal_ladder(p_final, target_size)

        # Early exit if nothing to do (no ideal ladder and no existing orders)
        current_prices = self.tracker.get_prices(side)
        if not ideal_ladder and not current_prices:
            return

        to_cancel_ids = []
        to_place = []  # List of (price, size)

        # === PHASE 1: Cancel stale orders ===
        for price in list(self.tracker.get_prices(side)):
            if price not in ideal_ladder:
                # Order is off-ladder (market moved away)
                orders_at_price = self.tracker.get_orders_at_price(side, price)
                to_cancel_ids.extend([o.order_id for o in orders_at_price])

        # === PHASE 2: Place/Stack/Shrink/Hold ===
        for price, target in ideal_ladder.items():
            current_size = self.tracker.get_total_size_at_price(side, price)

            if current_size == 0:
                # PLACE - no order exists (skip if below minimum)
                if target >= config.MIN_ORDER_SIZE:
                    to_place.append((price, target))

            elif current_size < target:
                # STACK - add difference (skip if diff below minimum)
                diff = target - current_size
                if diff >= config.MIN_ORDER_SIZE:
                    to_place.append((price, diff))

            elif current_size > target * (1 + config.HYSTERESIS):
                # SHRINK - too big, cancel all and replace
                orders_at_price = self.tracker.get_orders_at_price(side, price)
                to_cancel_ids.extend([o.order_id for o in orders_at_price])
                if target >= config.MIN_ORDER_SIZE:
                    to_place.append((price, target))

            # else: HOLD (within hysteresis tolerance)

        # === Execute cancels ===
        if to_cancel_ids:
            self.executor.cancel_orders(to_cancel_ids)
            # Remove from tracker
            self.tracker.remove_by_ids(side, to_cancel_ids)
            logger.info(f"[ORDER_MGR] Cancelled {len(to_cancel_ids)} {side.upper()} orders")

        # === Execute places ===
        if to_place:
            orders = [{"side": side.upper(), "price": price, "size": size} for price, size in to_place]
            placed = self.executor.place_orders_batch(orders)

            for side_str, price, order_id, size in placed:
                self.tracker.add(side_str.lower(), price, order_id, size)

            logger.info(f"[ORDER_MGR] Placed {len(placed)} {side.upper()} orders (p_final={p_final/10:.0f}c, target_size={target_size})")


    # =========================================================================
    # STATUS / DEBUG
    # =========================================================================

    def get_status(self) -> dict:
        """Get current status for logging."""
        summary = self.tracker.summary()
        return {
            "initialized": self._initialized,
            "yes_orders": summary["yes_count"],
            "no_orders": summary["no_count"],
            "yes_range": summary["yes_range"],
            "no_range": summary["no_range"],
            "yes_total_size": summary["yes_total_size"],
            "no_total_size": summary["no_total_size"],
        }
