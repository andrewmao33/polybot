"""
Real Order Executor using Polymarket CLOB API.
"""
import logging
import time
from typing import Dict, List, Tuple

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, PostOrdersArgs
from py_clob_client.order_builder.constants import BUY

from state.position_state import PositionState
import config

logger = logging.getLogger(__name__)


class RealExecutor:
    """Real order executor using Polymarket CLOB API."""

    def __init__(self, position: PositionState, private_key: str, proxy_wallet: str = ""):
        self.position = position

        if proxy_wallet:
            self.client = ClobClient(
                host=config.CLOB_HOST,
                chain_id=config.CHAIN_ID,
                key=private_key,
                signature_type=1,
                funder=proxy_wallet
            )
        else:
            self.client = ClobClient(
                host=config.CLOB_HOST,
                chain_id=config.CHAIN_ID,
                key=private_key
            )

        self._api_creds = self.client.create_or_derive_api_creds()
        self.client.set_api_creds(self._api_creds)

        self.fill_count = 0
        self.token_id_yes: str = ""
        self.token_id_no: str = ""
        self.market_id: str = ""

    def set_token_ids(self, token_id_yes: str, token_id_no: str, market_id: str = ""):
        """Set token IDs for YES and NO sides."""
        self.token_id_yes = token_id_yes
        self.token_id_no = token_id_no
        if market_id:
            self.market_id = market_id

    def place_orders_batch(self, orders: List[Dict]) -> List[Tuple[str, int, str, float]]:
        """
        Place orders using batch API (up to 15 orders per call).

        Returns: List of (side, price, order_id, size) for successfully placed orders.
        """
        start = time.time()
        placed_orders = []

        yes_orders = [o for o in orders if o["side"] == "YES"]
        no_orders = [o for o in orders if o["side"] == "NO"]

        for batch in self._chunk(yes_orders, 15):
            placed_orders.extend(self._place_batch(batch, "YES", self.token_id_yes))

        for batch in self._chunk(no_orders, 15):
            placed_orders.extend(self._place_batch(batch, "NO", self.token_id_no))

        elapsed_ms = (time.time() - start) * 1000
        logger.info(f"[PLACE] {len(placed_orders)} orders in {elapsed_ms:.0f}ms")
        return placed_orders

    def _place_batch(self, orders: List[Dict], side: str, token_id: str) -> List[Tuple[str, int, str, float]]:
        """
        Place a batch of orders for one side.

        Returns: List of (side, price, order_id, size) for successfully placed orders.
        """
        if not token_id or not orders:
            return []

        placed = []
        try:
            batch_args = []
            price_size_map = []  # Track (price, size) for each order in batch

            for order_dict in orders:
                price_decimal = order_dict["price"] / 1000.0
                order_args = OrderArgs(
                    price=price_decimal,
                    size=order_dict["size"],
                    side=BUY,
                    token_id=token_id
                )
                signed_order = self.client.create_order(order_args)
                batch_args.append(PostOrdersArgs(order=signed_order, orderType=OrderType.GTC))
                price_size_map.append((order_dict["price"], order_dict["size"]))

            response = self.client.post_orders(batch_args)

            if response:
                results = response if isinstance(response, list) else [response]
                for i, r in enumerate(results):
                    if isinstance(r, dict) and r.get("orderID"):
                        order_id = r["orderID"]
                        price, size = price_size_map[i]
                        placed.append((side.lower(), price, order_id, size))
                    elif isinstance(r, dict) and r.get("errorMsg"):
                        price, size = price_size_map[i]
                        logger.warning(f"[BATCH] Rejected {side} @ {price/10:.0f}c size={size}: {r.get('errorMsg')}")

            logger.info(f"[BATCH] Placed {len(placed)}/{len(orders)} {side} orders")
            return placed

        except Exception as e:
            logger.error(f"[BATCH] Error placing {side} batch: {e}")
            return []

    def _chunk(self, lst: List, n: int):
        """Split list into chunks of size n."""
        for i in range(0, len(lst), n):
            yield lst[i:i + n]

    def cancel_all_orders(self):
        """Cancel all orders on exchange. Loops until all cleared."""
        start = time.time()
        total = 0
        try:
            for _ in range(3):
                r = self.client.cancel_market_orders(market=self.market_id)
                batch = len(r.get("canceled", [])) if r else 0
                total += batch
                if batch == 0:
                    break

            elapsed_ms = (time.time() - start) * 1000
            if total > 0:
                logger.info(f"[CANCEL] {total} orders in {elapsed_ms:.0f}ms")

        except Exception as e:
            logger.warning(f"[CANCEL] Error: {e}")

    def cancel_orders(self, order_ids: List[str]) -> List[str]:
        """
        Cancel specific orders by ID.

        Args:
            order_ids: List of order IDs to cancel

        Returns:
            List of order IDs that were successfully cancelled
        """
        if not order_ids:
            return []

        start = time.time()
        try:
            response = self.client.cancel_orders(order_ids)
            cancelled = response.get("canceled", []) if response else []
            not_cancelled = response.get("not_canceled", {}) if response else {}

            elapsed_ms = (time.time() - start) * 1000

            if not_cancelled:
                for order_id, reason in not_cancelled.items():
                    logger.warning(f"[CANCEL] Failed {order_id[:10]}...: {reason}")

            logger.info(f"[CANCEL] {len(cancelled)}/{len(order_ids)} orders in {elapsed_ms:.0f}ms")
            return cancelled

        except Exception as e:
            logger.error(f"[CANCEL] Error cancelling orders: {e}")
            return []

    def handle_ws_fill(self, fill_event) -> bool:
        """Handle a fill event from the User WebSocket."""
        logger.debug(f"[EXEC DEBUG] handle_ws_fill called: asset={fill_event.asset_id[:20]}...")
        logger.debug(f"[EXEC DEBUG] token_yes={self.token_id_yes[:20]}... token_no={self.token_id_no[:20]}...")

        if fill_event.asset_id == self.token_id_yes:
            side = "YES"
        elif fill_event.asset_id == self.token_id_no:
            side = "NO"
        else:
            logger.debug(f"[EXEC DEBUG] Asset ID mismatch - ignoring fill")
            return False

        fill_price_ticks = fill_event.price * 1000
        fill_size = fill_event.size

        if side == "YES":
            self.position.Qy += fill_size
            self.position.Cy += fill_price_ticks * fill_size
        else:
            self.position.Qn += fill_size
            self.position.Cn += fill_price_ticks * fill_size

        self.fill_count += 1

        summary = self.get_position_summary()
        maker_tag = "MAKER" if fill_event.is_maker else "TAKER"
        logger.info(
            f"[FILL] {maker_tag} {side} {fill_size:.1f} @ ${fill_price_ticks/1000:.2f} | "
            f"Pos: Y:{summary['qty_yes']:.0f} N:{summary['qty_no']:.0f} | "
            f"Pair: ${summary['pair_cost']/1000:.3f} | "
            f"MinPnL: ${summary['min_pnl_usd']:+.2f}"
        )

        # Note: Imbalance control is handled by OrderManager via size scaling.
        # Do NOT call cancel_market_orders here - it bypasses the tracker
        # and causes ghost orders that we keep trying to cancel.

        return True

    def get_position_summary(self) -> Dict:
        """Get current position summary."""
        avg_yes = self.position.Cy / self.position.Qy if self.position.Qy > 0 else 0
        avg_no = self.position.Cn / self.position.Qn if self.position.Qn > 0 else 0
        pair_cost = avg_yes + avg_no
        imbalance = self.position.Qy - self.position.Qn

        total_cost = self.position.Cy + self.position.Cn
        min_payout = min(self.position.Qy, self.position.Qn) * 1000
        min_pnl = min_payout - total_cost

        return {
            "qty_yes": self.position.Qy,
            "qty_no": self.position.Qn,
            "cost_yes": self.position.Cy,
            "cost_no": self.position.Cn,
            "avg_yes": avg_yes,
            "avg_no": avg_no,
            "pair_cost": pair_cost,
            "imbalance": imbalance,
            "fill_count": self.fill_count,
            "min_pnl_usd": min_pnl / 1000
        }

    def reset(self):
        """Reset for new market."""
        self.cancel_all_orders()
        self.fill_count = 0
        self.position.reset()

    def get_api_credentials(self) -> Dict[str, str]:
        """Get API credentials for User WebSocket authentication."""
        return {
            "apiKey": self._api_creds.api_key,
            "secret": self._api_creds.api_secret,
            "passphrase": self._api_creds.api_passphrase
        }
