"""
Main strategy engine.
Orchestrates all priority levels and returns trade signals.
"""
from typing import Optional, List
from state.market_state import MarketState
from state.position_state import PositionState
from strategy.signals import TradeSignal
from strategy.continuous_arb import calculate_target_orders
from strategy.signals import TradeSignal


def evaluate_strategy(
    market_state: MarketState,
    position_state: PositionState,
    time_remaining_ms: Optional[float] = None
) -> Optional[List[TradeSignal]]:
    """
    Evaluates market state and position state using the Continuous Arbitrage logic.
    Returns targeted limit orders as TradeSignals.
    """
    # Don't trade if strike price is unknown
    if market_state.strike_price <= 0:
        return None
    
    # Calculate target limit orders
    target_orders = calculate_target_orders(
        position_state, 
        market_state, 
        time_remaining_ms
    )
    
    if not target_orders:
        return None
        
    signals = []
    for t in target_orders:
        signals.append(TradeSignal(
            side=t["side"],
            price=t["price"],
            size=t["size"],
            reason=t["reason"],
            priority=2 # Standard priority for inventory/arb
        ))
        
    return signals

