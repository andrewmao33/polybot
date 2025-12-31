"""
Position state management - tracks inventory and cost basis.
"""
from typing import Optional


class PositionState:
    """
    Tracks what we own in a market.
    Updated only by the Execution Layer (after fill confirmation).
    """
    
    def __init__(self, market_id: str):
        self.market_id = market_id
        
        # Quantities
        self.Qy: float = 0.0  # Quantity of YES shares
        self.Qn: float = 0.0  # Quantity of NO shares
        
        # Cost basis (in ticks)
        self.Cy: float = 0.0  # Total cost of YES shares
        self.Cn: float = 0.0  # Total cost of NO shares
        
        # In-flight locking (crucial for async safety)
        self.pending_yes: bool = False
        self.pending_no: bool = False
    
    def get_avg_y_ticks(self) -> Optional[float]:
        """Average cost per YES share in ticks."""
        if self.Qy <= 0:
            return None
        return self.Cy / self.Qy
    
    def get_avg_n_ticks(self) -> Optional[float]:
        """Average cost per NO share in ticks."""
        if self.Qn <= 0:
            return None
        return self.Cn / self.Qn
    
    def get_pair_cost_ticks(self) -> Optional[float]:
        """Total cost of a complete pair (YES + NO) in ticks."""
        avg_y = self.get_avg_y_ticks()
        avg_n = self.get_avg_n_ticks()
        if avg_y is None or avg_n is None:
            return None
        return avg_y + avg_n
    
    def is_empty(self) -> bool:
        """Check if position is empty."""
        return self.Qy == 0.0 and self.Qn == 0.0
    
    def has_both_sides(self) -> bool:
        """Check if holding both YES and NO."""
        return self.Qy > 0.0 and self.Qn > 0.0
    
    def has_only_yes(self) -> bool:
        """Check if holding only YES."""
        return self.Qy > 0.0 and self.Qn == 0.0
    
    def has_only_no(self) -> bool:
        """Check if holding only NO."""
        return self.Qn > 0.0 and self.Qy == 0.0
    
    def get_imbalance(self) -> float:
        """Get the absolute difference between YES and NO quantities."""
        return abs(self.Qy - self.Qn)
    
    def reset(self):
        """Reset position to empty state."""
        self.Qy = 0.0
        self.Qn = 0.0
        self.Cy = 0.0
        self.Cn = 0.0
        self.pending_yes = False
        self.pending_no = False

