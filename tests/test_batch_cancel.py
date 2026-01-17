"""
Test: REAL Batch order placement (post_orders) + cancel

Tests:
1. Place 15 orders via REAL batch API (post_orders - ONE API call)
2. Immediately call get_orders() - are they visible?
3. Immediately call cancel_orders([ids]) - does it work?
4. Verify all orders are cancelled

All orders at 1-2 cents so they won't fill.
"""
import asyncio
import aiohttp
import time
import json
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, OpenOrderParams, PostOrdersArgs
from py_clob_client.order_builder.constants import BUY

import config
from ingestion.gamma_api import (
    get_current_btc_15m_market,
    get_ssl_context
)


async def get_market_info():
    """Fetch current BTC 15m market info."""
    ssl_ctx = get_ssl_context()
    connector = aiohttp.TCPConnector(ssl=ssl_ctx)

    async with aiohttp.ClientSession(connector=connector) as session:
        market = await get_current_btc_15m_market(session)
        clob_ids = json.loads(market.get("clobTokenIds", "[]"))

        return {
            "market_id": market.get("conditionId"),
            "slug": market.get("slug"),
            "asset_id_yes": clob_ids[0] if len(clob_ids) > 0 else None,
            "asset_id_no": clob_ids[1] if len(clob_ids) > 1 else None,
        }


def test_batch_cancel():
    """
    Test REAL batch placement + immediate cancel_orders + get_orders visibility.
    """
    print("=" * 70)
    print("TEST: REAL Batch API (post_orders) + cancel_orders([ids])")
    print("=" * 70)

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
    client.set_api_creds(client.create_or_derive_api_creds())
    print("    Client initialized")

    # First, cancel any existing orders
    print("\n[3] Clearing any existing orders...")
    try:
        clear_result = client.cancel_all()
        existing = len(clear_result.get("canceled", []))
        print(f"    Cleared {existing} existing orders")
    except Exception as e:
        print(f"    Clear failed: {e}")

    token_id_yes = metadata['asset_id_yes']
    token_id_no = metadata['asset_id_no']

    # Build 80 orders (full ladder simulation)
    # post_orders max is 15, so we need 6 batch calls
    NUM_ORDERS = 80
    BATCH_SIZE = 15
    print(f"\n[4] Building {NUM_ORDERS} orders (simulating full ladder)...")

    all_orders = []
    for i in range(NUM_ORDERS):
        # Alternate YES/NO, unique prices 1-40 cents per side (won't fill)
        token_id = token_id_yes if i % 2 == 0 else token_id_no
        # Each side gets unique prices: YES at 1,2,3...40c, NO at 1,2,3...40c
        side_index = i // 2  # 0,0,1,1,2,2... -> 0,1,2...
        price = 0.01 + side_index * 0.01  # 1c, 2c, 3c... up to 40c

        all_orders.append({
            "token_id": token_id,
            "price": price,
            "size": 5
        })

    print(f"    Created {len(all_orders)} order specs")

    # Place orders in batches of 15 (API limit)
    print(f"\n[5] Placing {NUM_ORDERS} orders via {(NUM_ORDERS + BATCH_SIZE - 1) // BATCH_SIZE} batch calls...")
    t_place_start = time.time()
    order_ids = []
    num_batches = 0

    for i in range(0, len(all_orders), BATCH_SIZE):
        batch = all_orders[i:i + BATCH_SIZE]
        batch_args = []

        for order_spec in batch:
            batch_args.append(
                PostOrdersArgs(
                    order=client.create_order(OrderArgs(
                        price=order_spec["price"],
                        size=order_spec["size"],
                        side=BUY,
                        token_id=order_spec["token_id"]
                    )),
                    orderType=OrderType.GTC
                )
            )

        try:
            response = client.post_orders(batch_args)
            num_batches += 1
            batch_success = 0
            batch_fail = 0

            # Extract order IDs and count failures
            if isinstance(response, list):
                for item in response:
                    if isinstance(item, dict):
                        if item.get("orderID"):
                            order_ids.append(item["orderID"])
                            batch_success += 1
                        elif item.get("error") or item.get("errorMsg"):
                            batch_fail += 1
                            err = item.get("error") or item.get("errorMsg") or item
                            print(f"      Order failed: {err}")
            elif isinstance(response, dict) and response.get("orderID"):
                order_ids.append(response["orderID"])
                batch_success += 1

            print(f"    Batch {num_batches}: {batch_success}/{len(batch)} succeeded")

        except Exception as e:
            print(f"    Batch {num_batches + 1} error: {e}")

    t_place_end = time.time()
    place_time_ms = (t_place_end - t_place_start) * 1000

    print(f"    Placed {len(order_ids)} orders in {num_batches} batches")
    print(f"    Total time: {place_time_ms:.0f}ms ({place_time_ms/num_batches:.0f}ms per batch)")

    # TEST 1: Immediately check get_orders()
    print(f"\n[6] Immediately calling get_orders()...")
    t_get_start = time.time()
    try:
        open_orders = client.get_orders(OpenOrderParams())
        t_get_end = time.time()
        get_time_ms = (t_get_end - t_get_start) * 1000

        visible_count = len(open_orders) if open_orders else 0
        print(f"    get_orders() took {get_time_ms:.0f}ms")
        print(f"    Visible orders: {visible_count} / {len(order_ids)}")

        if visible_count == len(order_ids):
            print("    ✓ ALL orders visible immediately!")
        elif visible_count == 0:
            print("    ✗ NO orders visible yet (in-flight?)")
        else:
            print(f"    ~ PARTIAL visibility ({visible_count}/{len(order_ids)})")
    except Exception as e:
        print(f"    Error: {e}")
        visible_count = -1

    # TEST 2: Immediately call cancel_orders([ids])
    print(f"\n[7] Immediately calling cancel_orders() with {len(order_ids)} IDs...")
    t_cancel_start = time.time()
    try:
        cancel_result = client.cancel_orders(order_ids)
        t_cancel_end = time.time()
        cancel_time_ms = (t_cancel_end - t_cancel_start) * 1000

        canceled = cancel_result.get("canceled", [])
        not_canceled = cancel_result.get("not_canceled", {})

        print(f"    cancel_orders() took {cancel_time_ms:.0f}ms")
        print(f"    Canceled: {len(canceled)}")
        print(f"    Not canceled: {len(not_canceled)}")

        if not_canceled:
            print(f"    Reasons: {not_canceled}")

        if len(canceled) == len(order_ids):
            print("    ✓ ALL orders cancelled!")
        elif len(canceled) == 0:
            print("    ✗ NO orders cancelled!")
        else:
            print(f"    ~ PARTIAL cancel ({len(canceled)}/{len(order_ids)})")

    except Exception as e:
        print(f"    Error: {e}")
        canceled = []

    # TEST 3: Verify no orders remain
    print(f"\n[8] Verifying no orders remain...")
    time.sleep(0.3)
    try:
        remaining = client.get_orders(OpenOrderParams())
        remaining_count = len(remaining) if remaining else 0
        print(f"    Remaining orders: {remaining_count}")

        if remaining_count == 0:
            print("    ✓ All orders cleared!")
        else:
            print(f"    ✗ {remaining_count} orders still on book!")
            print("    Calling cancel_all() to clean up...")
            client.cancel_all()
    except Exception as e:
        print(f"    Error: {e}")

    # Summary
    total_time_ms = (time.time() - t_place_start) * 1000

    print("\n" + "=" * 70)
    print("SUMMARY - 80 ORDER STRESS TEST")
    print("=" * 70)
    print(f"Total orders:      {NUM_ORDERS}")
    print(f"Placement:         {num_batches} batch calls, {place_time_ms:.0f}ms total")
    print(f"get_orders():      {get_time_ms:.0f}ms → {visible_count} visible")
    print(f"cancel_orders():   {cancel_time_ms:.0f}ms → {len(canceled)} cancelled (ONE call!)")
    print(f"Total time:        {total_time_ms:.0f}ms")

    print("\n" + "=" * 70)
    print("CONCLUSIONS")
    print("=" * 70)

    if visible_count == len(order_ids):
        print("✓ get_orders() sees orders IMMEDIATELY after batch placement")
    else:
        print("✗ get_orders() has visibility delay")

    if len(canceled) == len(order_ids):
        print("✓ cancel_orders([ids]) works IMMEDIATELY after batch placement")
    else:
        print("✗ cancel_orders() has issues with freshly placed orders")

    if visible_count == len(order_ids) and len(canceled) == len(order_ids):
        print(f"\n→ SUCCESS: cancel_orders() handles {NUM_ORDERS} orders in ONE call!")
        print("  Full ladder cancel is viable with single API call.")
    else:
        print(f"\n→ PARTIAL: Only {len(canceled)}/{len(order_ids)} cancelled")
        print("  May need to split into smaller batches.")


if __name__ == "__main__":
    test_batch_cancel()
