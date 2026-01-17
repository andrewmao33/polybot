"""
Latency test for Polymarket API.
Run from local machine and VPS to compare.
"""
import time
import asyncio
import aiohttp
import ssl
import certifi
import statistics

CLOB_URL = "https://clob.polymarket.com"
DATA_URL = "https://data-api.polymarket.com"
GAMMA_URL = "https://gamma-api.polymarket.com"

async def ping_endpoint(session: aiohttp.ClientSession, url: str, name: str) -> float:
    """Ping an endpoint and return latency in ms."""
    start = time.perf_counter()
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            await resp.text()
            elapsed = (time.perf_counter() - start) * 1000
            return elapsed
    except Exception as e:
        print(f"  {name}: ERROR - {e}")
        return -1

async def test_endpoint(session: aiohttp.ClientSession, url: str, name: str, n: int = 10):
    """Test an endpoint multiple times and report stats."""
    print(f"\n{name} ({url})")
    print("-" * 50)

    latencies = []
    for i in range(n):
        lat = await ping_endpoint(session, url, name)
        if lat > 0:
            latencies.append(lat)
            print(f"  {i+1}: {lat:.1f}ms")
        await asyncio.sleep(0.1)  # Small delay between requests

    if latencies:
        print(f"\n  Min: {min(latencies):.1f}ms")
        print(f"  Max: {max(latencies):.1f}ms")
        print(f"  Avg: {statistics.mean(latencies):.1f}ms")
        print(f"  Median: {statistics.median(latencies):.1f}ms")
        if len(latencies) > 1:
            print(f"  Stdev: {statistics.stdev(latencies):.1f}ms")

    return latencies

async def main():
    print("=" * 50)
    print("POLYMARKET LATENCY TEST")
    print("=" * 50)

    ssl_ctx = ssl.create_default_context(cafile=certifi.where())
    conn = aiohttp.TCPConnector(ssl=ssl_ctx)
    async with aiohttp.ClientSession(connector=conn) as session:
        # Test each endpoint
        await test_endpoint(session, f"{CLOB_URL}/health", "CLOB API (health - lightweight)")
        await test_endpoint(session, f"{CLOB_URL}/books", "CLOB API (books - heavier)")
        await test_endpoint(session, f"{DATA_URL}/markets?limit=1", "Data API (markets)")
        await test_endpoint(session, f"{GAMMA_URL}/markets?limit=1", "Gamma API (markets)")

    print("\n" + "=" * 50)
    print("DONE - Compare these numbers between local and VPS")
    print("=" * 50)
    print("\nGood latency: <50ms")
    print("Okay latency: 50-150ms")
    print("Bad latency: >150ms")

if __name__ == "__main__":
    asyncio.run(main())
