"""
Simple test to verify ingestion is working correctly.
Run: python tests/test_ingestion.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import logging
from ingestion.orchestrator import IngestionOrchestrator
from state.market_state import MarketState
from state.position_state import PositionState

logging.basicConfig(level=logging.WARNING)

update_count = 0


def display(state: MarketState):
    global update_count
    update_count += 1

    print("\033[2J\033[H", end="")  # Clear screen
    print("=" * 50)
    print("  POLYMARKET INGESTION TEST")
    print("=" * 50)
    print(f"  Market: {state.slug}")
    print(f"  Strike: ${state.strike_price:,.2f}")
    print("-" * 50)
    print(f"  YES:  bid={state.best_bid_yes}  ask={state.best_ask_yes}")
    print(f"  NO:   bid={state.best_bid_no}  ask={state.best_ask_no}")
    print("-" * 50)
    print(f"  Updates: {update_count}")
    print("=" * 50)
    print("  Press Ctrl+C to stop")


def on_market_update(state: MarketState):
    display(state)


def on_market_switch(position: PositionState):
    print(f"\n[SWITCH] New market: {position.market_id}\n")


async def main():
    print("Starting ingestion test...")

    orchestrator = IngestionOrchestrator(
        on_market_state_update=on_market_update,
        on_position_state_reset=on_market_switch
    )

    try:
        await orchestrator.start()
    except KeyboardInterrupt:
        print("\nInterrupted")
    finally:
        await orchestrator.stop()


if __name__ == "__main__":
    asyncio.run(main())
