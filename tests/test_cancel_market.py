"""
Test: Compare cancel_market_orders by asset_id vs by market

Test 1: Place 20 orders (10 YES + 10 NO at 1-10c), cancel with 2 asset_id calls
Test 2: Place 20 orders (10 YES + 10 NO at 1-10c), cancel with 1 market call

Run when prices are high so 1-10c orders won't fill.
"""
import asyncio
import aiohttp
import time
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, OpenOrderParams, PostOrdersArgs
from py_clob_client.order_builder.constants import BUY

import config
from ingestion.gamma_api import get_current_btc_15m_market, get_ssl_context


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


def place_orders(client, token_id_yes, token_id_no, num_per_side=10):
    """Place orders on both sides from 1c to num_per_side cents."""
    order_ids = []

    # Build YES orders
    yes_batch = []
    for i in range(num_per_side):
        price = 0.01 + i * 0.01  # 1c, 2c, ... 10c
        yes_batch.append(
            PostOrdersArgs(
                order=client.create_order(OrderArgs(
                    price=price,
                    size=5,
                    side=BUY,
                    token_id=token_id_yes
                )),
                orderType=OrderType.GTC
            )
        )

    # Build NO orders
    no_batch = []
    for i in range(num_per_side):
        price = 0.01 + i * 0.01
        no_batch.append(
            PostOrdersArgs(
                order=client.create_order(OrderArgs(
                    price=price,
                    size=5,
                    side=BUY,
                    token_id=token_id_no
                )),
                orderType=OrderType.GTC
            )
        )

    # Place YES
    t_start = time.time()
    try:
        response = client.post_orders(yes_batch)
        if isinstance(response, list):
            for item in response:
                if isinstance(item, dict) and item.get("orderID"):
                    order_ids.append(item["orderID"])
    except Exception as e:
        print(f"    YES batch error: {e}")

    # Place NO
    try:
        response = client.post_orders(no_batch)
        if isinstance(response, list):
            for item in response:
                if isinstance(item, dict) and item.get("orderID"):
                    order_ids.append(item["orderID"])
    except Exception as e:
        print(f"    NO batch error: {e}")

    place_ms = (time.time() - t_start) * 1000
    return order_ids, place_ms


def cancel_by_asset_id(client, token_id_yes, token_id_no):
    """Cancel using 2 calls - one per asset_id."""
    t_start = time.time()
    total_canceled = 0

    # Cancel YES
    try:
        r = client.cancel_market_orders(asset_id=token_id_yes)
        total_canceled += len(r.get("canceled", []))
    except Exception as e:
        print(f"    YES cancel error: {e}")

    # Cancel NO
    try:
        r = client.cancel_market_orders(asset_id=token_id_no)
        total_canceled += len(r.get("canceled", []))
    except Exception as e:
        print(f"    NO cancel error: {e}")

    cancel_ms = (time.time() - t_start) * 1000
    return total_canceled, cancel_ms


def cancel_by_market(client, market_id):
    """Cancel using 1 call with market param."""
    t_start = time.time()
    total_canceled = 0

    try:
        r = client.cancel_market_orders(market=market_id)
        total_canceled = len(r.get("canceled", []))
    except Exception as e:
        print(f"    Market cancel error: {e}")

    cancel_ms = (time.time() - t_start) * 1000
    return total_canceled, cancel_ms


def verify_cleared(client):
    """Check no orders remain."""
    time.sleep(0.3)
    try:
        remaining = client.get_orders(OpenOrderParams())
        return len(remaining) if remaining else 0
    except:
        return -1


def test_cancel_comparison():
    """Compare cancel by asset_id vs cancel by market."""
    print("=" * 60)
    print("TEST: Cancel by asset_id (2 calls) vs market (1 call)")
    print("=" * 60)

    # Check credentials
    private_key = config.PRIVATE_KEY
    proxy_wallet = config.PROXY_WALLET

    if not private_key:
        print("ERROR: POLYMARKET_PRIVATE_KEY not set")
        return

    # Get market info
    print("\n[SETUP] Fetching current BTC 15m market...")
    metadata = asyncio.run(get_market_info())
    print(f"    Market: {metadata['slug']}")
    print(f"    Market ID: {metadata['market_id'][:20]}...")

    # Initialize CLOB client
    print("\n[SETUP] Initializing CLOB client...")
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

    # Clear any existing orders
    print("\n[SETUP] Clearing existing orders...")
    try:
        client.cancel_all()
    except:
        pass

    token_yes = metadata['asset_id_yes']
    token_no = metadata['asset_id_no']
    market_id = metadata['market_id']

    # =========================================================================
    # TEST 1: Cancel by asset_id (2 calls)
    # =========================================================================
    print("\n" + "=" * 60)
    print("TEST 1: Cancel by asset_id (2 calls)")
    print("=" * 60)

    print("\n[1a] Placing 20 orders (10 YES + 10 NO at 1-10c)...")
    order_ids_1, place_ms_1 = place_orders(client, token_yes, token_no)
    print(f"    Placed {len(order_ids_1)} orders in {place_ms_1:.0f}ms")

    print("\n[1b] Cancelling with 2 asset_id calls...")
    canceled_1, cancel_ms_1 = cancel_by_asset_id(client, token_yes, token_no)
    print(f"    Canceled {canceled_1} orders in {cancel_ms_1:.0f}ms")

    remaining_1 = verify_cleared(client)
    print(f"    Remaining: {remaining_1}")

    # =========================================================================
    # TEST 2: Cancel by market (1 call)
    # =========================================================================
    print("\n" + "=" * 60)
    print("TEST 2: Cancel by market (1 call)")
    print("=" * 60)

    print("\n[2a] Placing 20 orders (10 YES + 10 NO at 1-10c)...")
    order_ids_2, place_ms_2 = place_orders(client, token_yes, token_no)
    print(f"    Placed {len(order_ids_2)} orders in {place_ms_2:.0f}ms")

    print("\n[2b] Cancelling with 1 market call...")
    canceled_2, cancel_ms_2 = cancel_by_market(client, market_id)
    print(f"    Canceled {canceled_2} orders in {cancel_ms_2:.0f}ms")

    remaining_2 = verify_cleared(client)
    print(f"    Remaining: {remaining_2}")

    # =========================================================================
    # SUMMARY
    # =========================================================================
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"\nMethod 1: cancel_market_orders(asset_id=...) x2")
    print(f"    Placed:    {len(order_ids_1)} orders in {place_ms_1:.0f}ms")
    print(f"    Canceled:  {canceled_1} in {cancel_ms_1:.0f}ms (2 calls)")
    print(f"    Remaining: {remaining_1}")

    print(f"\nMethod 2: cancel_market_orders(market=...)")
    print(f"    Placed:    {len(order_ids_2)} orders in {place_ms_2:.0f}ms")
    print(f"    Canceled:  {canceled_2} in {cancel_ms_2:.0f}ms (1 call)")
    print(f"    Remaining: {remaining_2}")

    print(f"\nLatency difference: {cancel_ms_1 - cancel_ms_2:.0f}ms faster with market param")

    if remaining_1 == 0 and remaining_2 == 0:
        print("\nBoth methods cleared all orders successfully")
    else:
        if remaining_1 > 0:
            print(f"\nWARNING: asset_id method leaked {remaining_1} orders")
        if remaining_2 > 0:
            print(f"\nWARNING: market method leaked {remaining_2} orders")


if __name__ == "__main__":
    test_cancel_comparison()
