"""
Configuration for Polymarket trading bot.
"""
import os
from dotenv import load_dotenv

load_dotenv()  # Load .env file if present

# =============================================================================
# TRIPLE GATE STRATEGY PARAMETERS
# =============================================================================

# Position limits
MAX_POSITION = 75          # Max net exposure per side (replaces IMBALANCE_HARD)

# Order sizing
BASE_SIZE = 10             # Order size when neutral (shares)
MIN_ORDER_SIZE = 5         # Polymarket minimum order size

# Pricing
BASE_MARGIN_TICKS = 15     # 1.5c minimum profit margin
GAMMA = 0.001              # Skew sensitivity: 50 shares = 5c shift
MAX_SKEW_TICKS = 100       # 10c max price shift from anchor

# Ladder
LADDER_DEPTH = 5          # Number of rungs per side
MIN_PRICE = 100            # Minimum order price in ticks (10c floor)

# Execution
SLIPPAGE_TOL_TICKS = 20    # 2c max spread crossing when light
HYSTERESIS = 0.50          # 50% size tolerance before shrinking

# Profit lock
PROFIT_LOCK_MIN = 10.0     # Minimum guaranteed profit ($) to lock

# =============================================================================
# EXECUTION PARAMETERS
# =============================================================================

REFRESH_INTERVAL_MS = 2000   # Cancel + replace every 2 seconds
TICK_SIZE = 10               # Polymarket tick size (10 ticks = 1¢)

# =============================================================================
# SAFETY LIMITS
# =============================================================================

CIRCUIT_BREAKER_USD = 200.0  # Emergency stop if total cost exceeds this

# =============================================================================
# API ENDPOINTS
# =============================================================================

CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet
POLYMARKET_WS_MARKET_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
POLYMARKET_WS_USER_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"

# =============================================================================
# CREDENTIALS
# =============================================================================

PRIVATE_KEY = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
PROXY_WALLET = os.environ.get("POLYMARKET_PROXY_WALLET", "")
