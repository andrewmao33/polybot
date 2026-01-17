"""
Raw WebSocket message viewer.
Run: python tests/test_raw_ws.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import json
import ssl
import websockets

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

# Get token IDs from gamma API
async def get_token_ids():
    from ingestion.gamma_api import get_current_btc_15m_market, extract_market_metadata, get_ssl_context
    import aiohttp

    ssl_ctx = get_ssl_context()
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=ssl_ctx)) as session:
        market = await get_current_btc_15m_market(session)
        metadata = await extract_market_metadata(market)
        return metadata["clob_token_ids"]


async def main():
    token_ids = await get_token_ids()
    print(f"Token IDs: {token_ids}\n")

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    async with websockets.connect(WS_URL, ssl=ssl_ctx) as ws:
        # Subscribe
        msg = {"assets_ids": token_ids, "operation": "subscribe", "custom_feature_enabled": True}
        await ws.send(json.dumps(msg))
        print("Subscribed. Waiting for messages...\n")

        count = 0
        async for message in ws:
            data = json.loads(message)

            # Handle list (initial books)
            if isinstance(data, list):
                print(f"[{count}] LIST with {len(data)} items (initial books)")
                print(data)
                count += 1
                continue

            event_type = data.get("event_type", "unknown")
            if event_type == "best_bid_ask":
                print(f"[{count}] {event_type}: {json.dumps(data, indent=2)[:500]}")
                count += 1

            if count > 20:
                print("\n...stopping after 20 messages")
                break


if __name__ == "__main__":
    asyncio.run(main())
