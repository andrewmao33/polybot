"""
Order state management - tracks order lifecycle and fills.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Literal
from enum import Enum
import time


class OrderStatus(Enum):
    """Order status enumeration."""
    PENDING = "PENDING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass
class Fill:
    """Represents a single fill event."""
    timestamp_ms: int
    size: float
    price: float  # Fill price in ticks


@dataclass
class OrderState:
    """
    Tracks the state of an order through its lifecycle.
    
    Attributes:
        order_id: Unique order identifier
        side: "YES" or "NO"
        price: Limit price in ticks (0-1000)
        size: Original order size (shares)
        filled_size: Total size filled so far
        status: Current order status
        timestamp_ms: Order submission timestamp (milliseconds)
        avg_fill_price: Average fill price in ticks (None if not filled)
        fills: List of individual fill events
    """
    order_id: str
    side: Literal["YES", "NO"]
    price: float  # Limit price in ticks
    size: float   # Original size
    filled_size: float = 0.0
    status: OrderStatus = OrderStatus.PENDING
    timestamp_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    avg_fill_price: Optional[float] = None
    fills: List[Fill] = field(default_factory=list)
    
    def add_fill(self, fill_size: float, fill_price: float):
        """
        Add a fill to this order.
        
        Args:
            fill_size: Size of this fill
            fill_price: Price of this fill (in ticks)
        """
        if fill_size <= 0:
            raise ValueError(f"Fill size must be positive, got {fill_size}")
        if fill_price <= 0 or fill_price > 1000:
            raise ValueError(f"Fill price must be between 0 and 1000, got {fill_price}")
        
        # Add fill event
        fill = Fill(
            timestamp_ms=int(time.time() * 1000),
            size=fill_size,
            price=fill_price
        )
        self.fills.append(fill)
        
        # Update filled size
        self.filled_size += fill_size
        
        # Update average fill price
        total_cost = sum(f.size * f.price for f in self.fills)
        self.avg_fill_price = total_cost / self.filled_size
        
        # Update status
        if self.filled_size >= self.size:
            self.status = OrderStatus.FILLED
        elif self.filled_size > 0:
            self.status = OrderStatus.PARTIALLY_FILLED
    
    def get_remaining_size(self) -> float:
        """Get remaining size to fill."""
        return max(0.0, self.size - self.filled_size)
    
    def is_complete(self) -> bool:
        """Check if order is fully filled or cancelled/rejected."""
        return self.status in [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED]
    
    def cancel(self):
        """Cancel this order."""
        if self.status == OrderStatus.FILLED:
            raise ValueError("Cannot cancel a filled order")
        self.status = OrderStatus.CANCELLED

