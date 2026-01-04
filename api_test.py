from typing import Any


import asyncio
import aiohttp
import websockets
import json
import ssl
import time

GAMMA = "https://gamma-api.polymarket.com"
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

market_dict = {} # market id -> market slug
current_market = None
next_market = None

# Disable SSL verification for testing
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

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
    start = int(time.time())
    start -= start % 900
    
    slug = f"btc-updown-15m-{start}"
    m = await fetch_market_by_slug(session, slug)
    if m is not None:
        market_dict[m["conditionId"]] = slug
        return m
    
    raise RuntimeError("Could not find current BTC 15m market (tried current/next/prev window).")

async def get_next_btc_15m_market(session: aiohttp.ClientSession, now: int | None = None) -> dict:
    start = int(time.time())
    start = (start - (start % 900)) + 900
    
    slug = f"btc-updown-15m-{start}"
    m = await fetch_market_by_slug(session, slug)
    if m is not None:
        market_dict[m["conditionId"]] = slug
        return m
    
    raise RuntimeError("Could not find next BTC 15m market.")


async def websocket_handler(session: aiohttp.ClientSession, ws):
    global current_market
    print("Starting websocket_handler...")
    curr_assets_ids = json.loads(current_market["clobTokenIds"])
    sub_msg = {
        "assets_ids": curr_assets_ids,
        "operation": "subscribe",
        "custom_feature_enabled": True
    }
    print(f"Sending subscription: {sub_msg}")
    await ws.send(json.dumps(sub_msg))
    print("Subscription sent, waiting for messages...")
    
    # Calculate next market start time (30 seconds before it opens)
    current_start = int(current_market["slug"].split("-")[-1])
    next_start = current_start + 900
    wait_time = (next_start - time.time()) - 300
    
    # Schedule unsubscribe and subscribe concurrently
    async def unsubscribe_current():
        unsub_msg = {
            "assets_ids": curr_assets_ids,
            "operation": "unsubscribe",
            "custom_feature_enabled": True
        }
        await ws.send(json.dumps(unsub_msg))
        print(f"UNSUBSCRIBED FROM CURRENT MARKET: {current_market['slug']}")
    
    async def subscribe_next():
        next_market_data = await get_next_btc_15m_market(session)
        next_assets_ids = json.loads(next_market_data["clobTokenIds"])
        next_sub_msg = {
            "assets_ids": next_assets_ids,
            "operation": "subscribe",
            "custom_feature_enabled": True
        }
        await ws.send(json.dumps(next_sub_msg))
        print(f"SUBSCRIBED TO NEXT MARKET: {next_market_data['slug']}")
        return next_market_data
    
    async def switch_markets():
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        # Run both operations concurrently
        next_market_data, _ = await asyncio.gather(
            subscribe_next(),
            unsubscribe_current()
        )
        # Update current_market only after both complete
        global current_market
        current_market = next_market_data
        print(f"MARKET SWITCHED: {current_market['slug']}")
    
    asyncio.create_task(switch_markets())
    

    try:
        async for message in ws:
            if not message:
                continue
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue
            
            if isinstance(data, list):
                print("message: subscription_confirmation (list)")
            elif isinstance(data, dict):
                #print("message:", data.get('event_type', 'unknown'))
                #print("market:", data.get('market', 'N/A'))
                print(data.get('event_type'))
                if "winning_outcome" in data:
                    print("\n" + "="*60)
                    print("MARKET RESOLVED MESSAGE:")
                    print("="*60)
                    print(json.dumps(data, indent=4))
                    print("="*60)
                    print("\nStopping...")
                    return  # Exit the handler
                print("time:", time.time())
                print("slug:", market_dict.get(data.get('market', 'N/A'), 'N/A'))
            else:
                print(f"message: unknown type {type(data)}")
    except websockets.exceptions.ConnectionClosed:
        print("WebSocket connection closed")
    except Exception as e:
        print(f"Error in message loop: {e}")


async def main():
    global current_market
    print("Starting main...")
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_context)) as session:
            print("Fetching current market...")
            current_market = await get_current_btc_15m_market(session)
            print(f"Got market: {current_market.get('slug', 'N/A')}")
            
            print("Connecting to WebSocket...")
            async with websockets.connect(WS_URL, ssl=ssl_context) as ws:
                print("WebSocket connected!")
                await websocket_handler(session, ws)
    except Exception as e:
        print(f"Error in main: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
