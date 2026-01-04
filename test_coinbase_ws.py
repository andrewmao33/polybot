"""
Test script for Coinbase WebSocket connection (BTC price).
"""
import asyncio
import json
import logging
import ssl
import websockets

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Coinbase WebSocket URL for BTC-USD ticker
COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"

# SSL context (disable verification for testing)
ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE


async def test_coinbase():
    """Test Coinbase WebSocket connection."""
    logger.info("Connecting to Coinbase WebSocket...")
    
    try:
        async with websockets.connect(
            COINBASE_WS_URL,
            ssl=ssl_context,
            ping_interval=20,
            ping_timeout=10
        ) as ws:
            logger.info("✅ Connected to Coinbase WebSocket")
            
            # Subscribe to BTC-USD ticker
            subscribe_msg = {
                "type": "subscribe",
                "product_ids": ["BTC-USD"],
                "channels": ["ticker"]
            }
            await ws.send(json.dumps(subscribe_msg))
            logger.info("Subscribed to BTC-USD ticker")
            logger.info("Listening for BTC price updates...\n")
            
            # Listen for messages
            async for message in ws:
                try:
                    data = json.loads(message)
                    
                    # Handle ticker messages
                    if data.get("type") == "ticker":
                        price = float(data.get("price", 0))
                        volume_24h = float(data.get("volume_24h", 0))
                        high_24h = float(data.get("high_24h", 0))
                        low_24h = float(data.get("low_24h", 0))
                        
                        logger.info(
                            f"BTC Price: ${price:,.2f} | "
                            f"24h Vol: ${volume_24h:,.0f} | "
                            f"24h High: ${high_24h:,.2f} | "
                            f"24h Low: ${low_24h:,.2f}"
                        )
                    elif data.get("type") == "subscriptions":
                        logger.info(f"Subscription confirmed: {data}")
                    else:
                        logger.debug(f"Other message: {data}")
                        
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse message: {e}")
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    
    except websockets.exceptions.ConnectionClosed:
        logger.warning("Connection closed")
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)


if __name__ == "__main__":
    try:
        asyncio.run(test_coinbase())
    except KeyboardInterrupt:
        logger.info("\nTest interrupted by user")
