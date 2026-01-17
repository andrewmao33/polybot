"""
Continuous Arbitrage Strategy.

Goal: Buy YES + NO shares where pair_cost < $1.00
      Profit = $1.00 - pair_cost (per paired share)

Core Logic:
1. Calculate max_price based on current position to ensure profitability
2. Place maker orders from config.MIN_PRICE up to max_price
3. Adjust sizing based on imbalance to stay balanced
4. Exit when profitable, near expiry, or unrecoverable
"""
from typing import List, Dict, Optional
from state.position_state import PositionState
from state.market_state import MarketState
import config
import logging

logger = logging.getLogger(__name__)


def calculate_target_orders(
    position: PositionState,
    market: MarketState,
    time_remaining_ms: Optional[float] = None
) -> List[Dict]:
    """
    Calculate orders to place.

    Returns empty list to stop trading (exit conditions met).
    """
    # Need asks to calculate max prices
    ask_yes = market.get_best_ask_yes()
    ask_no = market.get_best_ask_no()

    if ask_yes is None or ask_no is None:
        return []

    # Current position
    avg_yes = position.Cy / position.Qy if position.Qy > 0 else 0
    avg_no = position.Cn / position.Qn if position.Qn > 0 else 0
    imbalance = position.Qy - position.Qn
    paired = min(position.Qy, position.Qn)

    # =========================================================================
    # EXIT CONDITIONS
    # =========================================================================

    # Profit lock: guaranteed payout > total cost + minimum threshold
    # min(Qy, Qn) shares will pay out $1.00 each regardless of outcome
    total_cost = position.Cy + position.Cn
    guaranteed_payout = paired * 1000  # In ticks
    profit = (guaranteed_payout - total_cost) / 1000
    if profit >= config.PROFIT_LOCK_MIN:
        logger.info(f"PROFIT LOCK: guaranteed ${profit:.2f} profit")
        return []

    # =========================================================================
    # MAX PRICE CALCULATION
    # =========================================================================
    # max_price = min(ask - 1¢, 100¢ - avg_other - 2¢)
    #
    # Constraint 1: ask - 1¢ ensures order rests as maker
    # Constraint 2: 100¢ - avg_other - 2¢ ensures pair_cost < 98¢

    if avg_no > 0:
        max_yes = min(ask_yes - 10, 1000 - avg_no - config.PROFIT_MARGIN)
    else:
        max_yes = ask_yes - 10  # No NO position yet, just stay maker

    if avg_yes > 0:
        max_no = min(ask_no - 10, 1000 - avg_yes - config.PROFIT_MARGIN)
    else:
        max_no = ask_no - 10  # No YES position yet, just stay maker

    # Floor at config.MIN_PRICE
    max_yes = max(max_yes, config.MIN_PRICE)
    max_no = max(max_no, config.MIN_PRICE)

    # =========================================================================
    # IMBALANCE SIZING
    # =========================================================================
    # |imbalance| < 20:  Normal both sides
    # |imbalance| < 50:  Heavy side 0.5x, light side 1.5x
    # |imbalance| >= 50: Stop heavy side entirely

    base_size = config.LADDER_SIZE

    if abs(imbalance) < config.IMBALANCE_SOFT:
        size_yes = base_size
        size_no = base_size
    elif abs(imbalance) < config.IMBALANCE_HARD:
        if imbalance > 0:  # Heavy on YES
            size_yes = base_size * 0.5
            size_no = base_size * 1.5
        else:  # Heavy on NO
            size_yes = base_size * 1.5
            size_no = base_size * 0.5
    else:
        if imbalance > 0:  # Heavy on YES - stop YES entirely
            size_yes = 0
            size_no = base_size
        else:  # Heavy on NO - stop NO entirely
            size_yes = base_size
            size_no = 0

    # =========================================================================
    # GENERATE LADDER ORDERS
    # =========================================================================
    # Place orders from config.MIN_PRICE up to max_price with LADDER_SPACING

    orders = []

    if size_yes > 0:
        price = config.MIN_PRICE
        while price <= max_yes:
            orders.append({
                "side": "YES",
                "price": int(price),
                "size": float(size_yes)
            })
            price += config.LADDER_SPACING

    if size_no > 0:
        price = config.MIN_PRICE
        while price <= max_no:
            orders.append({
                "side": "NO",
                "price": int(price),
                "size": float(size_no)
            })
            price += config.LADDER_SPACING

    return orders
