#!/usr/bin/env python3
"""
Test User WebSocket fill notifications using UserWebSocket class.

Connects to the current BTC 15-min market and prints fill events.
Manually trade to verify fills are received.

Usage:
    export POLYMARKET_PRIVATE_KEY="0x..."
    export POLYMARKET_PROXY_WALLET="0x..."
    python tests/test_user_ws.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import json
import os
import ssl
import aiohttp
from py_clob_client.client import ClobClient

import config
from ingestion.gamma_api import get_current_btc_15m_market
from ingestion.user_ws import UserWebSocket, FillEvent

# SSL context for macOS
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE


def on_fill(fill: FillEvent):
    """Callback when a fill is received."""
    print("\n" + "=" * 60)
    print(f">>> FILL RECEIVED!")
    print(f"    Side: {fill.side}")
    print(f"    Size: {fill.size}")
    print(f"    Price: ${fill.price:.2f}")
    print(f"    Asset: {fill.asset_id[:20]}...")
    print(f"    Maker: {fill.is_maker}")
    print(f"    Order: {fill.order_id[:20]}...")
    print("=" * 60)


async def main():
    private_key = os.environ.get("POLYMARKET_PRIVATE_KEY")
    proxy_wallet = os.environ.get("POLYMARKET_PROXY_WALLET", "")

    if not private_key:
        print("Set POLYMARKET_PRIVATE_KEY environment variable")
        return

    if not proxy_wallet:
        print("Set POLYMARKET_PROXY_WALLET environment variable")
        return

    # Create CLOB client to get API credentials
    print("Creating CLOB client...")
    if proxy_wallet:
        client = ClobClient(
            host=config.CLOB_HOST,
            chain_id=config.CHAIN_ID,
            key=private_key,
            signature_type=1,
            funder=proxy_wallet
        )
    else:
        client = ClobClient(
            host=config.CLOB_HOST,
            chain_id=config.CHAIN_ID,
            key=private_key
        )

    creds = client.create_or_derive_api_creds()
    client.set_api_creds(creds)

    print(f"API Key: {creds.api_key[:20]}...")
    print(f"Maker Address: {proxy_wallet}")

    # Get current market
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)
    async with aiohttp.ClientSession(connector=connector) as session:
        print("\nFetching current BTC 15min market...")
        metadata = await get_current_btc_15m_market(session)

        condition_id = metadata["conditionId"]
        slug = metadata["slug"]
        tokens = json.loads(metadata["clobTokenIds"])

        print(f"Market: {slug}")
        print(f"Condition ID: {condition_id}")
        print(f"YES token: {tokens[0]}")
        print(f"NO token: {tokens[1]}")

    # Create UserWebSocket
    print("\n" + "=" * 60)
    print("Creating UserWebSocket...")
    print("=" * 60)

    user_ws = UserWebSocket(
        api_key=creds.api_key,
        api_secret=creds.api_secret,
        api_passphrase=creds.api_passphrase,
        maker_address=proxy_wallet,
        on_fill=on_fill
    )
    user_ws.set_market(condition_id)

    print("\nListening for fills. Place a trade manually to test.")
    print("Press Ctrl+C to stop.\n")

    try:
        await user_ws.connect()
    except KeyboardInterrupt:
        print("\nStopping...")
        await user_ws.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
