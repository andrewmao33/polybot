"""
Main strategy engine.
Orchestrates all priority levels and returns trade signals.
"""
from typing import Optional, List
from state.market_state import MarketState
from state.position_state import PositionState
from strategy.signals import TradeSignal
from strategy.stages import (
    check_synthetic_arbitrage,
    apply_oracle_filter,
    check_bootstrap_stage,
    check_hedging_stage,
    check_averaging_down_stage,
    check_profit_lock,
    check_stop_loss
)


def evaluate_strategy(
    market_state: MarketState,
    position_state: PositionState
) -> Optional[List[TradeSignal]]:
    """
    Main strategy evaluation function.
    
    Evaluates market state and position state, then returns trade signals
    based on priority levels (0-3).
    
    Priority Order:
    0. Synthetic Arbitrage (risk-free, bypasses oracle filter)
    1. Oracle Filter (applied to all directional trades)
    2. Inventory Management (Bootstrap, Hedging, Averaging Down)
    3. Bailout & Lock (Stop Loss, Profit Lock)
    
    Args:
        market_state: Current market state snapshot
        position_state: Current position state
    
    Returns:
        List of TradeSignals, or None if:
        - No signals generated
        - Profit is locked (halt trading)
        - Stop loss triggered (execution layer should handle selling)
    """
    # Priority 3: Check profit lock first (highest priority safety check)
    if check_profit_lock(market_state, position_state):
        # Profit is locked - halt all trading
        return None
    
    # Priority 3: Check stop loss
    if check_stop_loss(market_state, position_state):
        # Stop loss triggered - execution layer should handle selling
        # We return None here since TradeSignal only supports buys
        return None
    
    # Priority 0: Check for synthetic arbitrage (risk-free opportunity)
    arbitrage_signals = check_synthetic_arbitrage(market_state, position_state)
    if arbitrage_signals:
        # Arbitrage signals bypass oracle filter (they're risk-free)
        return arbitrage_signals
    
    # Collect signals from Priority 2 stages (inventory management)
    inventory_signals: List[TradeSignal] = []
    
    # Check hedging stage (rebalancing) - highest priority in inventory management
    hedging_signal = check_hedging_stage(market_state, position_state)
    if hedging_signal:
        inventory_signals.append(hedging_signal)
    
    # If not hedging, check other stages
    if not inventory_signals:
        # Check bootstrap stage (legging in)
        bootstrap_signal = check_bootstrap_stage(market_state, position_state)
        if bootstrap_signal:
            inventory_signals.append(bootstrap_signal)
        
        # Check averaging down stage
        avg_down_signal = check_averaging_down_stage(market_state, position_state)
        if avg_down_signal:
            inventory_signals.append(avg_down_signal)
    
    # Priority 1: Apply oracle filter to inventory management signals
    if inventory_signals:
        filtered_signals = apply_oracle_filter(market_state, inventory_signals)
        if filtered_signals:
            return filtered_signals
    
    # No signals generated
    return None

