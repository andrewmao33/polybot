"""
Configuration constants for the trading bot.
"""

# Strategy Constants
MAX_SHARES = 100  # Hard cap on exposure per side (~$50 risk)
BALANCE_PAD = 10  # Max allowable share mismatch before forced hedging
TARGET_PAIR = 980  # Target cost for a complete pair ($0.98 in ticks)
STOP_LOSS = 0.25  # Price (25¢) to panic-sell a solo position
FLOOR_THRESH = 0.20  # Do not "average down" if price drops below this
BASE_SENSE = 50  # Oracle sensitivity divisor
MAX_TRADE = 20  # Maximum size of a single order

# Time-based thresholds (in minutes remaining)
BOOTSTRAP_THRESHOLD_HIGH = 0.50  # Buy if price < 0.50 when T > 5m (relaxed from 0.40)
BOOTSTRAP_THRESHOLD_LOW = 0.30   # Buy if price < 0.30 when 2m < T < 5m (relaxed from 0.15)
BOOTSTRAP_KILL_ZONE = 2  # No entry if T < 2m
BOOTSTRAP_HIGH_VOL_ZONE = 5  # High volatility zone threshold

# Oracle Filter Thresholds
# Relaxed: Allow more trades by widening the block zones
ORACLE_BLOCK_YES = 0.30  # Block YES buys if Model < 0.30 (was 0.40 - more permissive)
ORACLE_BLOCK_NO = 0.70   # Block NO buys if Model > 0.70 (was 0.60 - more permissive)

# Execution Constants
LATENCY_MS = 0.150  # Simulated network round-trip time (150ms)
PAYOUT_TICKS = 1000  # Payout per winning share (1000 ticks = $1.00)

# WebSocket Endpoints
POLYMARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"

# API Endpoints
GAMMA_API_BASE = "https://gamma-api.polymarket.com"

