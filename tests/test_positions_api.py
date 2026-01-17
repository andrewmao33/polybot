#!/usr/bin/env python3
"""
Test the data-api /positions endpoint.
Polls every 5 seconds and displays position updates.

Usage:
    export POLYMARKET_PROXY_WALLET="0x..."
    python tests/test_positions_api.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import json
import os
import ssl
import aiohttp
from ingestion.gamma_api import get_current_btc_15m_market


async def main():
    proxy_wallet = os.environ.get("POLYMARKET_PROXY_WALLET")
    if not proxy_wallet:
        print("Set POLYMARKET_PROXY_WALLET environment variable")
        return

    # SSL context for macOS
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)

    async with aiohttp.ClientSession(connector=connector) as session:
        # Get current market
        print("Fetching current BTC 15min market...")
        metadata = await get_current_btc_15m_market(session)

        condition_id = metadata["conditionId"]
        slug = metadata["slug"]
        tokens = json.loads(metadata["clobTokenIds"])

        print(f"Market: {slug}")
        print(f"Condition ID: {condition_id}")
        print(f"YES token: {tokens[0]}")
        print(f"NO token: {tokens[1]}")
        print(f"Proxy wallet: {proxy_wallet}")
        print("-" * 60)
        print("Polling positions every 5 seconds. Ctrl+C to stop.\n")

        url = "https://data-api.polymarket.com/positions"
        params = {
            "user": proxy_wallet,
            "market": condition_id,
            "sizeThreshold": 0
        }

        while True:
            try:
                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        print(f"API error: {resp.status}")
                    else:
                        positions = await resp.json()

                        yes_size = 0
                        yes_avg = 0
                        no_size = 0
                        no_avg = 0

                        for pos in positions:
                            asset = pos.get("asset")
                            size = float(pos.get("size", 0))
                            avg = float(pos.get("avgPrice", 0))

                            if asset == tokens[0]:  # YES
                                yes_size = size
                                yes_avg = avg
                            elif asset == tokens[1]:  # NO
                                no_size = size
                                no_avg = avg

                        pair_cost = yes_avg + no_avg if yes_size > 0 and no_size > 0 else 0
                        print(f"YES: {yes_size:>6.1f} @ ${yes_avg:.3f}  |  NO: {no_size:>6.1f} @ ${no_avg:.3f}  |  Pair: ${pair_cost:.3f}")

            except Exception as e:
                print(f"Error: {e}")

            await asyncio.sleep(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped")
