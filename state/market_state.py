"""
Market state management - atomic representation of order book and market data.
"""
from sortedcontainers import SortedDict
from typing import Optional
import time


class MarketState:
    """
    Atomic state of a market, updated by WebSocket handlers.
    Represents the "Absolute Truth" of the market at tick T.
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
        
        # Order books: price (in ticks, 0-1000) -> size
        self.order_book_yes_bids = SortedDict()  # {price_ticks: size}
        self.order_book_yes_asks = SortedDict()  # {price_ticks: size}
        self.order_book_no_bids = SortedDict()   # {price_ticks: size}
        self.order_book_no_asks = SortedDict()   # {price_ticks: size}
        
        # Asset IDs for YES and NO tokens
        self.asset_id_yes: Optional[str] = None
        self.asset_id_no: Optional[str] = None
        
        # Oracle data
        self.btc_price: Optional[float] = None
        
        # Timestamps
        self.exchange_timestamp: Optional[int] = None  # Latest from WebSocket (ms)
        self.local_timestamp: Optional[int] = None     # Local system time (ms)
        self.clock_skew: Optional[int] = None          # Local - Exchange (ms)
        
        # Sync status - track if we have book snapshots for both sides
        self.sync_status_yes = False
        self.sync_status_no = False
    
    @property
    def sync_status(self) -> bool:
        """True if we have valid snapshots for both YES and NO."""
        return self.sync_status_yes and self.sync_status_no
    
    def get_best_bid_yes(self) -> Optional[float]:
        """Get best bid price for YES token in ticks (0-1000)."""
        if not self.order_book_yes_bids:
            return None
        return self.order_book_yes_bids.peekitem(-1)[0]  # Highest bid
    
    def get_best_ask_yes(self) -> Optional[float]:
        """Get best ask price for YES token in ticks (0-1000)."""
        if not self.order_book_yes_asks:
            return None
        return self.order_book_yes_asks.peekitem(0)[0]  # Lowest ask
    
    def get_best_bid_size_yes(self) -> float:
        """Get best bid size for YES token."""
        if not self.order_book_yes_bids:
            return 0.0
        return self.order_book_yes_bids.peekitem(-1)[1]
    
    def get_best_ask_size_yes(self) -> float:
        """Get best ask size for YES token."""
        if not self.order_book_yes_asks:
            return 0.0
        return self.order_book_yes_asks.peekitem(0)[1]
    
    def get_best_bid_no(self) -> Optional[float]:
        """Get best bid price for NO token in ticks (0-1000)."""
        if not self.order_book_no_bids:
            return None
        return self.order_book_no_bids.peekitem(-1)[0]  # Highest bid
    
    def get_best_ask_no(self) -> Optional[float]:
        """Get best ask price for NO token in ticks (0-1000)."""
        if not self.order_book_no_asks:
            return None
        return self.order_book_no_asks.peekitem(0)[0]  # Lowest ask
    
    def get_best_bid_size_no(self) -> float:
        """Get best bid size for NO token."""
        if not self.order_book_no_bids:
            return 0.0
        return self.order_book_no_bids.peekitem(-1)[1]
    
    def get_best_ask_size_no(self) -> float:
        """Get best ask size for NO token."""
        if not self.order_book_no_asks:
            return 0.0
        return self.order_book_no_asks.peekitem(0)[1]
    
    def get_time_remaining_seconds(self) -> Optional[int]:
        """Calculate time remaining until market expiration in seconds."""
        if self.exchange_timestamp is None:
            return None
        remaining_ms = self.end_timestamp - self.exchange_timestamp
        return max(0, remaining_ms // 1000)
    
    def get_time_remaining_minutes(self) -> Optional[float]:
        """Calculate time remaining until market expiration in minutes."""
        seconds = self.get_time_remaining_seconds()
        if seconds is None:
            return None
        return seconds / 60.0
    
    def update_clock_skew(self):
        """Update clock skew based on exchange timestamp."""
        if self.exchange_timestamp is not None:
            self.local_timestamp = int(time.time() * 1000)
            self.clock_skew = self.local_timestamp - self.exchange_timestamp
    
    def snapshot(self) -> 'MarketState':
        """
        Create a snapshot copy of the current state for strategy evaluation.
        This ensures strategy sees atomic state without mutations during evaluation.
        """
        snapshot = MarketState(self.market_id, self.strike_price, self.end_timestamp)
        snapshot.order_book_yes_bids = SortedDict(self.order_book_yes_bids)
        snapshot.order_book_yes_asks = SortedDict(self.order_book_yes_asks)
        snapshot.order_book_no_bids = SortedDict(self.order_book_no_bids)
        snapshot.order_book_no_asks = SortedDict(self.order_book_no_asks)
        snapshot.asset_id_yes = self.asset_id_yes
        snapshot.asset_id_no = self.asset_id_no
        snapshot.btc_price = self.btc_price
        snapshot.exchange_timestamp = self.exchange_timestamp
        snapshot.local_timestamp = self.local_timestamp
        snapshot.clock_skew = self.clock_skew
        snapshot.sync_status_yes = self.sync_status_yes
        snapshot.sync_status_no = self.sync_status_no
        return snapshot

