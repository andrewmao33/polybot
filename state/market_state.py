"""
Market state management - tracks best bid/ask for YES and NO tokens.
"""
from typing import Optional


class MarketState:
    """
    State of a market, updated by WebSocket handlers.
    Only tracks best bid/ask - no full orderbook depth needed.
    """

    def __init__(self, market_id: str, strike_price: float, end_timestamp: int):
        """
        Initialize market state.

        Args:
            market_id: Condition ID of the market
            strike_price: Strike price for the market (e.g., 100000 for $100k)
            end_timestamp: Market expiration timestamp in milliseconds
        """
        self.market_id = market_id
        self.strike_price = strike_price
        self.end_timestamp = end_timestamp  # Market expiration timestamp (ms)

        # Best bid/ask prices in ticks (0-1000)
        self.best_bid_yes: Optional[float] = None
        self.best_ask_yes: Optional[float] = None
        self.best_bid_no: Optional[float] = None
        self.best_ask_no: Optional[float] = None

        # Asset IDs for YES and NO tokens
        self.asset_id_yes: Optional[str] = None
        self.asset_id_no: Optional[str] = None

        # Market slug for identification
        self.slug: Optional[str] = None

        # Timestamp from WebSocket (ms)
        self.exchange_timestamp: Optional[int] = None

        # Sync status - track if we have data for both sides
        self.sync_status_yes = False
        self.sync_status_no = False

    @property
    def sync_status(self) -> bool:
        """True if we have valid data for both YES and NO."""
        return self.sync_status_yes and self.sync_status_no

    # Getter methods for backward compatibility
    def get_best_bid_yes(self) -> Optional[float]:
        """Get best bid price for YES token in ticks (0-1000)."""
        return self.best_bid_yes

    def get_best_ask_yes(self) -> Optional[float]:
        """Get best ask price for YES token in ticks (0-1000)."""
        return self.best_ask_yes

    def get_best_bid_no(self) -> Optional[float]:
        """Get best bid price for NO token in ticks (0-1000)."""
        return self.best_bid_no

    def get_best_ask_no(self) -> Optional[float]:
        """Get best ask price for NO token in ticks (0-1000)."""
        return self.best_ask_no

    def get_time_remaining_seconds(self) -> Optional[int]:
        """Calculate time remaining until market expiration in seconds."""
        if self.exchange_timestamp is None:
            return None
        remaining_ms = self.end_timestamp - self.exchange_timestamp
        return max(0, remaining_ms // 1000)

    def snapshot(self) -> 'MarketState':
        """
        Create a snapshot copy for strategy evaluation.
        Ensures strategy sees consistent state without mid-evaluation mutations.
        """
        snap = MarketState(self.market_id, self.strike_price, self.end_timestamp)
        snap.best_bid_yes = self.best_bid_yes
        snap.best_ask_yes = self.best_ask_yes
        snap.best_bid_no = self.best_bid_no
        snap.best_ask_no = self.best_ask_no
        snap.asset_id_yes = self.asset_id_yes
        snap.asset_id_no = self.asset_id_no
        snap.slug = self.slug
        snap.exchange_timestamp = self.exchange_timestamp
        snap.sync_status_yes = self.sync_status_yes
        snap.sync_status_no = self.sync_status_no
        return snap
