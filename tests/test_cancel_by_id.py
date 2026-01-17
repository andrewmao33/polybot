"""
Test: Can we cancel an order by ID immediately after placing?

This tests whether the cancel-by-ID API works immediately after order placement,
or if there's a processing delay that prevents immediate cancellation.

The order is placed at 1 cent so it will NOT get filled.
"""
import asyncio
import aiohttp
import time
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY

import config
from ingestion.gamma_api import (
    get_current_btc_15m_market,
    extract_market_metadata,
    get_ssl_context
)


async def get_market_info():
    """Fetch current BTC 15m market info."""
    import json
    ssl_ctx = get_ssl_context()
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)

    async with aiohttp.ClientSession(connector=connector) as session:
        market = await get_current_btc_15m_market(session)

        # Extract token IDs directly from clobTokenIds
        # Format: ["up_token_id", "down_token_id"]
        clob_ids = json.loads(market.get("clobTokenIds", "[]"))

        return {
            "market_id": market.get("conditionId"),
            "slug": market.get("slug"),
            "asset_id_yes": clob_ids[0] if len(clob_ids) > 0 else None,  # Up = Yes
            "asset_id_no": clob_ids[1] if len(clob_ids) > 1 else None,   # Down = No
        }


def test_cancel_by_id():
    """
    Test immediate cancel by order ID.

    Steps:
    1. Get current market
    2. Place 1 order at 1 cent (YES side)
    3. Immediately cancel by ID
    4. Report timing and results
    """
    print("=" * 60)
    print("TEST: Cancel Order by ID - Immediate Cancel")
    print("=" * 60)

    # Check credentials
    private_key = config.PRIVATE_KEY
    proxy_wallet = config.PROXY_WALLET

    if not private_key:
        print("ERROR: POLYMARKET_PRIVATE_KEY not set")
        return

    print(f"Proxy wallet: {proxy_wallet[:10]}..." if proxy_wallet else "No proxy wallet")

    # Get market info
    print("\n[1] Fetching current BTC 15m market...")
    metadata = asyncio.run(get_market_info())

    print(f"    Market: {metadata['slug']}")
    print(f"    Condition ID: {metadata['market_id'][:20]}...")
    print(f"    YES Token: {metadata['asset_id_yes'][:20]}...")
    print(f"    NO Token: {metadata['asset_id_no'][:20]}...")

    # Initialize CLOB client
    print("\n[2] Initializing CLOB client...")
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

    # Derive API credentials
    client.set_api_creds(client.create_or_derive_api_creds())
    print("    Client initialized")

    # Place order at 1 cent (0.01) - will NOT get filled
    print("\n[3] Placing order at 1 cent (will not fill)...")

    token_id = metadata['asset_id_yes']
    price = 0.01  # 1 cent - way below any realistic ask
    size = 5      # Minimum size

    order_args = OrderArgs(
        price=price,
        size=size,
        side=BUY,
        token_id=token_id
    )

    signed_order = client.create_order(order_args)

    t_place_start = time.time()
    response = client.post_order(signed_order, OrderType.GTC)
    t_place_end = time.time()

    place_time_ms = (t_place_end - t_place_start) * 1000

    if not response or not response.get("orderID"):
        print(f"    ERROR: Order placement failed: {response}")
        return

    order_id = response["orderID"]
    print(f"    Order placed in {place_time_ms:.0f}ms")
    print(f"    Order ID: {order_id}")

    # Immediately cancel by ID
    print("\n[4] Immediately cancelling by ID...")

    t_cancel_start = time.time()
    cancel_result = client.cancel(order_id=order_id)
    t_cancel_end = time.time()

    cancel_time_ms = (t_cancel_end - t_cancel_start) * 1000
    total_time_ms = (t_cancel_end - t_place_start) * 1000

    print(f"    Cancel took {cancel_time_ms:.0f}ms")
    print(f"    Total (place + cancel): {total_time_ms:.0f}ms")
    print(f"    Cancel result: {cancel_result}")

    # Analyze result
    print("\n[5] Analysis:")

    canceled = cancel_result.get("canceled", [])
    not_canceled = cancel_result.get("not_canceled", {})

    if order_id in canceled:
        print("    ✓ SUCCESS: Order was cancelled immediately!")
        print(f"    → Cancel-by-ID works right after placement")
    elif order_id in not_canceled:
        reason = not_canceled[order_id]
        print(f"    ✗ FAILED: Order not cancelled")
        print(f"    → Reason: {reason}")
    else:
        print(f"    ? UNKNOWN: Order ID not in response")
        print(f"    → canceled: {canceled}")
        print(f"    → not_canceled: {not_canceled}")

    # Verify order is gone
    print("\n[6] Verifying order is cancelled...")
    try:
        order_status = client.get_order(order_id)
        if order_status:
            status = order_status.get("status", "unknown")
            print(f"    Order status: {status}")
            if status == "CANCELED":
                print("    ✓ Confirmed: Order shows as CANCELED")
            elif status == "LIVE":
                print("    ✗ WARNING: Order is still LIVE!")
            else:
                print(f"    ? Order in state: {status}")
        else:
            print("    Order not found (likely cancelled)")
    except Exception as e:
        print(f"    Error checking order: {e}")

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Place time:  {place_time_ms:.0f}ms")
    print(f"Cancel time: {cancel_time_ms:.0f}ms")
    print(f"Total time:  {total_time_ms:.0f}ms")

    if order_id in canceled:
        print("\nCONCLUSION: Immediate cancel-by-ID WORKS!")
        print("No need to wait for order to 'process' before cancelling.")
    else:
        print("\nCONCLUSION: Immediate cancel FAILED")
        print("May need to wait for order to process before cancelling.")


if __name__ == "__main__":
    test_cancel_by_id()
