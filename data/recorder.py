"""
Simple data recorder for backtesting.
Records market state snapshots.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict
from state.market_state import MarketState

logger = logging.getLogger(__name__)


class DataRecorder:
    """
    Records market state snapshots for backtesting.
    
    Records:
    - market_data.jsonl: Market state snapshots (one per line)
    - metadata.json: Session metadata
    """
    
    def __init__(self, data_dir: str = "recorded_data"):
        """
        Initialize data recorder.
        
        Args:
            data_dir: Directory to save recorded data
        """
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        
        # Current recording session
        self.session_id: Optional[str] = None
        self.session_dir: Optional[Path] = None
        self.recording = False
        self.data_file = None
        
        # Track markets seen
        self.markets_seen: set = set()
    
    def start_recording(self, market_id: str) -> str:
        """
        Start a new recording session.
        
        Args:
            market_id: Initial market ID being recorded
        
        Returns:
            Session ID
        """
        if self.recording:
            logger.warning("Already recording, stopping previous session")
            self.stop_recording()
        
        # Create session directory
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        self.session_id = f"{market_id}_{timestamp}"
        self.session_dir = self.data_dir / self.session_id
        self.session_dir.mkdir(exist_ok=True)
        
        # Open data file for writing
        data_file_path = self.session_dir / "market_data.jsonl"
        self.data_file = open(data_file_path, "w")
        
        self.markets_seen = {market_id}
        
        # Save metadata
        metadata = {
            "session_id": self.session_id,
            "start_time": timestamp,
            "start_timestamp_ms": int(datetime.utcnow().timestamp() * 1000),
            "initial_market_id": market_id,
            "data_format": "minimal",
            "fields": [
                "timestamp_ms",
                "market_id",
                "yes_bid", "yes_bid_size",
                "yes_ask", "yes_ask_size",
                "no_bid", "no_bid_size",
                "no_ask", "no_ask_size",
                "btc_price",
                "strike_price",
                "end_timestamp",
                "sync_status"
            ]
        }
        with open(self.session_dir / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)
        
        self.recording = True
        logger.info(f"📹 Started recording session: {self.session_id}")
        
        return self.session_id
    
    def stop_recording(self):
        """Stop recording and save all data."""
        if not self.recording:
            return
        
        self.recording = False
        
        # Close data file
        if self.data_file:
            self.data_file.close()
            self.data_file = None
        
        # Update metadata with end time
        metadata_file = self.session_dir / "metadata.json"
        if metadata_file.exists():
            with open(metadata_file, "r") as f:
                metadata = json.load(f)
            metadata["end_time"] = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            metadata["end_timestamp_ms"] = int(datetime.utcnow().timestamp() * 1000)
            metadata["markets_seen"] = list(self.markets_seen)
            with open(metadata_file, "w") as f:
                json.dump(metadata, f, indent=2)
        
        logger.info(f"📹 Stopped recording session: {self.session_id}")
        self.session_id = None
        self.session_dir = None
        self.markets_seen = set()
    
    def record_market_state(self, market_state: MarketState):
        """
        Record a market state snapshot.
        
        Args:
            market_state: Current market state
        """
        if not self.recording or not self.data_file:
            return
        
        # Only record if books are synced
        if not market_state.sync_status:
            return
        
        # Get best bid/ask prices and sizes
        yes_bid = market_state.get_best_bid_yes()
        yes_bid_size = market_state.get_best_bid_size_yes() if yes_bid else 0.0
        yes_ask = market_state.get_best_ask_yes()
        yes_ask_size = market_state.get_best_ask_size_yes() if yes_ask else 0.0
        no_bid = market_state.get_best_bid_no()
        no_bid_size = market_state.get_best_bid_size_no() if no_bid else 0.0
        no_ask = market_state.get_best_ask_no()
        no_ask_size = market_state.get_best_ask_size_no() if no_ask else 0.0
        
        # Need at least some prices to record
        if yes_bid is None and yes_ask is None and no_bid is None and no_ask is None:
            return
        
        # Track market ID
        self.markets_seen.add(market_state.market_id)
        
        # Create data point
        data_point = {
            "timestamp_ms": market_state.exchange_timestamp or int(datetime.utcnow().timestamp() * 1000),
            "market_id": market_state.market_id,
            "yes_bid": float(yes_bid) if yes_bid is not None else None,
            "yes_bid_size": float(yes_bid_size),
            "yes_ask": float(yes_ask) if yes_ask is not None else None,
            "yes_ask_size": float(yes_ask_size),
            "no_bid": float(no_bid) if no_bid is not None else None,
            "no_bid_size": float(no_bid_size),
            "no_ask": float(no_ask) if no_ask is not None else None,
            "no_ask_size": float(no_ask_size),
            "btc_price": float(market_state.btc_price) if market_state.btc_price else None,
            "strike_price": float(market_state.strike_price) if market_state.strike_price else 0.0,
            "end_timestamp": market_state.end_timestamp,
            "sync_status": market_state.sync_status
        }
        
        # Write to file (one line per snapshot)
        self.data_file.write(json.dumps(data_point) + "\n")
        self.data_file.flush()

