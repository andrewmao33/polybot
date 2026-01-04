"""
Gamma API client for fetching Polymarket market data.
Used to discover active 15-minute BTC markets.
"""
import aiohttp
import ssl
import time
from typing import Optional, Dict
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"

# SSL context for testing (disable verification)
_ssl_context = ssl.create_default_context()
_ssl_context.check_hostname = False
_ssl_context.verify_mode = ssl.CERT_NONE


def floor_to_15min_epoch(ts: int) -> int:
    """Floor timestamp to nearest 15-minute boundary (900 seconds)."""
    return ts - (ts % 900)


async def fetch_market_by_slug(
    session: aiohttp.ClientSession, 
    slug: str
) -> Optional[Dict]:
    """
    Fetch market data by slug.
    
    Args:
        session: aiohttp session
        slug: Market slug (e.g., 'btc-updown-15m-1767126600')
    
    Returns:
        Market data dict or None if not found
    """
    url = f"{GAMMA_BASE}/markets/slug/{slug}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 200:
                return await r.json()
            if r.status == 404:
                return None
            r.raise_for_status()
    except Exception as e:
        logger.error(f"Error fetching market {slug}: {e}")
        return None


async def get_current_btc_15m_market(
    session: aiohttp.ClientSession, 
    now: Optional[int] = None
) -> Dict:
    """
    Get the current active BTC 15-minute market.
    
    Args:
        session: aiohttp session
        now: Current timestamp (defaults to current time)
    
    Returns:
        Market data dict
    
    Raises:
        RuntimeError: If market not found
    """
    now = now or int(time.time())
    start = floor_to_15min_epoch(now)
    
    slug = f"btc-updown-15m-{start}"
    market = await fetch_market_by_slug(session, slug)
    
    if market is not None:
        return market
    
    raise RuntimeError(
        f"Could not find current BTC 15m market (tried slug: {slug})"
    )


async def get_next_btc_15m_market(
    session: aiohttp.ClientSession, 
    now: Optional[int] = None
) -> Dict:
    """
    Get the next BTC 15-minute market.
    
    Args:
        session: aiohttp session
        now: Current timestamp (defaults to current time)
    
    Returns:
        Market data dict
    
    Raises:
        RuntimeError: If market not found
    """
    now = now or int(time.time())
    start = (now - (now % 900)) + 900
    
    slug = f"btc-updown-15m-{start}"
    market = await fetch_market_by_slug(session, slug)
    
    if market is not None:
        return market
    
    raise RuntimeError(
        f"Could not find next BTC 15m market (tried slug: {slug})"
    )


async def extract_market_metadata(market: Dict) -> Dict:
    """
    Extract relevant metadata from Gamma API market response.
    
    Args:
        market: Market data from Gamma API
    
    Returns:
        Dict with:
        - market_id: Condition ID
        - asset_id_yes: YES token ID
        - asset_id_no: NO token ID
        - strike_price: Strike price (0 for Up/Down markets, set at runtime)
        - end_timestamp: Market expiration timestamp (ms)
        - clob_token_ids: List of CLOB token IDs
        - is_updown: True if this is an Up/Down market
    """
    import json
    
    condition_id = market.get("conditionId")
    clob_token_ids = json.loads(market.get("clobTokenIds", "[]"))
    
    # For Up/Down markets, strike price is not in description
    # Will be set to BTC price when market starts (first book sync)
    strike_price = 0.0
    description = market.get("description", "")
    
    # Parse end date
    end_date_str = market.get("endDate")
    if end_date_str:
        try:
            end_date = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            end_timestamp = int(end_date.timestamp() * 1000)
        except Exception as e:
            logger.warning(f"Could not parse end date: {end_date_str}, error: {e}")
            end_timestamp = 0
    else:
        end_timestamp = 0
    
    # Get token IDs (YES and NO)
    asset_id_yes = None
    asset_id_no = None
    
    tokens = market.get("tokens", [])
    for token in tokens:
        outcome = token.get("outcome", "").upper()
        token_id = token.get("tokenId")
        if outcome == "YES" or outcome == "YES TOKEN":
            asset_id_yes = token_id
        elif outcome == "NO" or outcome == "NO TOKEN":
            asset_id_no = token_id
    
    # Extract slug from market
    slug = market.get("slug", "")
    
    return {
        "market_id": condition_id,
        "asset_id_yes": asset_id_yes,
        "asset_id_no": asset_id_no,
        "strike_price": strike_price,
        "end_timestamp": end_timestamp,
        "clob_token_ids": clob_token_ids,
        "description": description,
        "slug": slug,
        "active": market.get("active", False),
        "closed": market.get("closed", False),
    }




def get_ssl_context() -> ssl.SSLContext:
    """Get SSL context for API requests."""
    return _ssl_context

