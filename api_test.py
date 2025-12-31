import asyncio
import aiohttp
import websockets
import json
import ssl
import time

GAMMA = "https://gamma-api.polymarket.com"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# Disable SSL verification for testing
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

def floor_to_15min_epoch(ts: int) -> int:
    # 15 min = 900 seconds
    return ts - (ts % 900)

async def fetch_market_by_slug(session: aiohttp.ClientSession, slug: str) -> dict | None:
    url = f"{GAMMA}/markets/slug/{slug}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status == 200:
                return await r.json()
            if r.status == 404:
                return None
            r.raise_for_status()
    except Exception as e:
        print(f"Error fetching market {slug}: {e}")
        return None

async def get_current_btc_15m_market(session: aiohttp.ClientSession, now: int | None = None) -> dict:
    now = now or int(time.time())
    start = floor_to_15min_epoch(now)
    
    slug = f"btc-updown-15m-{start}"
    m = await fetch_market_by_slug(session, slug)
    if m is not None:
        return m
    
    raise RuntimeError("Could not find current BTC 15m market (tried current/next/prev window).")

async def websocket_handler(market: dict):
    clob_token_ids = json.loads(market["clobTokenIds"])
    
    print(f"\n{'='*60}")
    print("MARKET STATUS:")
    print(f"  Active: {market.get('active', 'unknown')}")
    print(f"  End Date: {market.get('endDate', 'unknown')}")
    print(f"  Start Date: {market.get('startDate', 'unknown')}")
    print(f"  Resolved: {market.get('resolved', 'unknown')}")
    print(f"  Closed: {market.get('closed', 'unknown')}")
    print(f"{'='*60}\n")
    
    print(f"Connecting to WebSocket...")
    print(f"Token IDs: {clob_token_ids}")
    
    try:
        async with websockets.connect(WS_URL, ssl=ssl_context) as ws:
            print("✅ WebSocket connection opened!")
            
            # Subscribe to the market channel
            sub_msg = {
                "assets_ids": clob_token_ids,
                "type": "market"
            }
            print("Sending subscription message:", json.dumps(sub_msg, indent=2))
            await ws.send(json.dumps(sub_msg))
            print("Subscription sent, waiting for messages...\n")
            
            # Listen for messages
            async for message in ws:
                try:
                    data = json.loads(message)
                    print("PARSED DATA:", json.dumps(data, indent=2))
                except json.JSONDecodeError:
                    print("Non-JSON message:", message)
                print("="*60 + "\n")
                
    except websockets.exceptions.ConnectionClosed:
        print("🔌 WebSocket connection closed")
    except Exception as e:
        print(f"❌ WebSocket error: {e}")

async def main():
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
        market = await get_current_btc_15m_market(session)
        print(f"Found market: {market.get('description', 'N/A')}")
        await websocket_handler(market)

if __name__ == "__main__":
    asyncio.run(main())
