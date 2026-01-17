"""
Order Tracker - Tracks standing orders for place-and-hold strategy.

Key design:
- Price is the primary key, but MULTIPLE orders per price (for stacking)
- Stores list of (order_id, remaining_size) for each price
- Supports aggregation of total size per price level
"""
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class StandingOrder:
    """Represents a standing order in the book."""
    order_id: str
    price: int          # price in ticks (0-1000)
    remaining_size: float
    original_size: float


class OrderTracker:
    """
    Tracks standing orders for both YES and NO sides.

    Supports multiple orders per price level (stacking).
    Primary key is price, but each price can have a list of orders.
    """

    def __init__(self):
        # {price_ticks: [StandingOrder, ...]}
        self._yes_orders: dict[int, list[StandingOrder]] = {}
        self._no_orders: dict[int, list[StandingOrder]] = {}

    def _get_orders(self, side: str) -> dict[int, list[StandingOrder]]:
        """Get the order dict for a side."""
        return self._yes_orders if side == "yes" else self._no_orders

    # =========================================================================
    # ADD / REMOVE / UPDATE
    # =========================================================================

    def add(self, side: str, price: int, order_id: str, size: float):
        """Add a new order to tracking (appends to list at this price)."""
        orders = self._get_orders(side)
        if price not in orders:
            orders[price] = []
        orders[price].append(StandingOrder(
            order_id=order_id,
            price=price,
            remaining_size=size,
            original_size=size
        ))
        logger.info(f"[TRACKER] +{side.upper()} @ {price/10:.0f}c size={size} id={order_id[:8]}...")

    def remove(self, side: str, price: int) -> list[StandingOrder]:
        """Remove all orders at a price. Returns removed orders."""
        orders = self._get_orders(side)
        removed = orders.pop(price, [])
        if removed:
            logger.info(f"[TRACKER] -{side.upper()} @ {price/10:.0f}c REMOVED {len(removed)} orders")
        return removed

    def remove_by_id(self, side: str, order_id: str) -> Optional[StandingOrder]:
        """Remove a specific order by ID. Returns the removed order or None."""
        orders = self._get_orders(side)
        for price, order_list in orders.items():
            for i, order in enumerate(order_list):
                if order.order_id == order_id:
                    removed = order_list.pop(i)
                    # Clean up empty price levels
                    if not order_list:
                        del orders[price]
                    logger.info(f"[TRACKER] -{side.upper()} @ {price/10:.0f}c id={order_id[:8]} REMOVED")
                    return removed
        return None

    def find_by_order_id(self, side: str, order_id: str) -> Optional[int]:
        """Find price for an order_id. Returns None if not found."""
        orders = self._get_orders(side)
        for price, order_list in orders.items():
            for order in order_list:
                if order.order_id == order_id:
                    return price
        return None

    def update_fill(self, side: str, price: int, filled_size: float, order_id: str):
        """
        Update remaining size after a fill. Removes order if fully filled.

        Args:
            side: "yes" or "no"
            price: Fill price (may differ from placed price for taker fills)
            filled_size: Size that was filled
            order_id: Order ID from WebSocket (required to identify which order)
        """
        # Find the order by ID (handles taker fills where price differs)
        actual_price = self.find_by_order_id(side, order_id)
        if not actual_price:
            logger.info(f"[TRACKER] Fill for unknown order {order_id[:8]} (already removed)")
            return

        if actual_price != price:
            logger.info(f"[TRACKER] Taker fill: reported@{price/10:.0f}c, order@{actual_price/10:.0f}c")

        # Find and update the order in the list
        orders = self._get_orders(side)
        order_list = orders[actual_price]
        for i, order in enumerate(order_list):
            if order.order_id == order_id:
                order.remaining_size -= filled_size

                if order.remaining_size <= 0.001:  # Float tolerance
                    order_list.pop(i)
                    if not order_list:
                        del orders[actual_price]
                    logger.info(f"[TRACKER] -{side.upper()} @ {actual_price/10:.0f}c FULLY FILLED")
                else:
                    logger.info(f"[TRACKER] ~{side.upper()} @ {actual_price/10:.0f}c partial, remaining={order.remaining_size:.1f}")
                return

        logger.warning(f"[TRACKER] Order {order_id[:8]} not found in list at {actual_price/10:.0f}c")

    def clear(self, side: str):
        """Clear all orders for a side."""
        orders = self._get_orders(side)
        count = sum(len(ol) for ol in orders.values())
        orders.clear()
        logger.info(f"[TRACKER] Cleared {count} {side.upper()} orders")

    def clear_all(self):
        """Clear all orders for both sides."""
        self.clear("yes")
        self.clear("no")

    # =========================================================================
    # QUERIES
    # =========================================================================

    def get_orders_at_price(self, side: str, price: int) -> list[StandingOrder]:
        """Get all orders at a specific price."""
        return self._get_orders(side).get(price, [])

    def get_total_size_at_price(self, side: str, price: int) -> float:
        """Get total size of all orders at a price."""
        order_list = self._get_orders(side).get(price, [])
        return sum(o.remaining_size for o in order_list)

    def get_prices(self, side: str) -> set[int]:
        """Get all prices with standing orders."""
        return set(self._get_orders(side).keys())

    def get_all_orders(self, side: str) -> list[StandingOrder]:
        """Get all standing orders for a side (flattened)."""
        orders = self._get_orders(side)
        return [order for order_list in orders.values() for order in order_list]

    def get_orders_above(self, side: str, max_price: int) -> list[StandingOrder]:
        """Get all orders with price > max_price (flattened)."""
        orders = self._get_orders(side)
        result = []
        for price, order_list in orders.items():
            if price > max_price:
                result.extend(order_list)
        return result

    def get_orders_below(self, side: str, min_price: int) -> list[StandingOrder]:
        """Get all orders with price < min_price (flattened)."""
        orders = self._get_orders(side)
        result = []
        for price, order_list in orders.items():
            if price < min_price:
                result.extend(order_list)
        return result

    def get_orders_in_range(self, side: str, min_price: int, max_price: int) -> list[StandingOrder]:
        """Get all orders with min_price <= price <= max_price (flattened)."""
        orders = self._get_orders(side)
        result = []
        for price, order_list in orders.items():
            if min_price <= price <= max_price:
                result.extend(order_list)
        return result

    def count(self, side: str) -> int:
        """Count standing orders for a side."""
        return sum(len(ol) for ol in self._get_orders(side).values())

    def total_count(self) -> int:
        """Count total standing orders."""
        return self.count("yes") + self.count("no")

    def get_top_price(self, side: str) -> Optional[int]:
        """Get the highest price with a standing order."""
        orders = self._get_orders(side)
        return max(orders.keys()) if orders else None

    def get_bottom_price(self, side: str) -> Optional[int]:
        """Get the lowest price with a standing order."""
        orders = self._get_orders(side)
        return min(orders.keys()) if orders else None

    # =========================================================================
    # BULK OPERATIONS
    # =========================================================================

    def add_batch(self, side: str, orders_data: list[tuple[int, str, float]]):
        """
        Add multiple orders at once.
        orders_data: list of (price, order_id, size)
        """
        for price, order_id, size in orders_data:
            self.add(side, price, order_id, size)

    def remove_by_ids(self, side: str, order_ids: list[str]) -> list[StandingOrder]:
        """Remove multiple orders by ID. Returns removed orders."""
        removed = []
        for order_id in order_ids:
            order = self.remove_by_id(side, order_id)
            if order:
                removed.append(order)
        return removed

    # =========================================================================
    # DEBUG / STATUS
    # =========================================================================

    def summary(self) -> dict:
        """Get a summary of tracked orders."""
        yes_prices = sorted(self._yes_orders.keys())
        no_prices = sorted(self._no_orders.keys())

        return {
            "yes_count": self.count("yes"),
            "no_count": self.count("no"),
            "yes_range": (min(yes_prices), max(yes_prices)) if yes_prices else (0, 0),
            "no_range": (min(no_prices), max(no_prices)) if no_prices else (0, 0),
            "yes_total_size": sum(o.remaining_size for o in self.get_all_orders("yes")),
            "no_total_size": sum(o.remaining_size for o in self.get_all_orders("no")),
        }

    def __repr__(self) -> str:
        s = self.summary()
        return (
            f"OrderTracker(YES: {s['yes_count']} orders {s['yes_range']}, "
            f"NO: {s['no_count']} orders {s['no_range']})"
        )
