"""
Oracle model price calculation.
Calculates the "fair" price based on BTC price vs strike price.
"""
from typing import Optional
from state.market_state import MarketState
import config


def calculate_model_price(market_state: MarketState) -> Optional[float]:
    """
    Calculate the model price based on BTC price, strike price, and time remaining.
    
    The model price represents what the "fair" price should be:
    - If BTC > Strike: Model > 500 ticks (favoring YES)
    - If BTC < Strike: Model < 500 ticks (favoring NO)
    - If BTC = Strike: Model = 500 ticks (neutral)
    
    Formula:
        Scaling = BASE_SENSE × (1 + Time_rem_minutes / 15)
        Diff = (BTC_price - Strike_price) / Scaling
        Model = clamp(500 + Diff, 10, 990)  # in ticks
    
    Args:
        market_state: Current market state with BTC price and strike price
    
    Returns:
        Model price in ticks (10 to 990), or None if insufficient data
    """
    # Check if we have required data
    if market_state.btc_price is None:
        return None
    
    if market_state.strike_price is None or market_state.strike_price <= 0:
        return None
    
    time_remaining_minutes = market_state.get_time_remaining_minutes()
    if time_remaining_minutes is None:
        return None
    
    # Calculate dynamic scaling factor
    # Scaling increases as time remaining decreases (more sensitive near expiration)
    scaling = config.BASE_SENSE * (1 + time_remaining_minutes / 15)
    
    # Calculate price difference
    price_diff = market_state.btc_price - market_state.strike_price
    
    # Calculate model price adjustment in ticks
    # The scaling factor converts dollar difference to tick difference
    diff_ticks = price_diff / scaling
    
    # Base model is 500 ticks (neutral = $0.50), adjust based on price difference
    model_price_ticks = 500 + diff_ticks
    
    # Clamp to valid range (10 to 990 ticks = $0.01 to $0.99)
    model_price_ticks = max(10, min(990, model_price_ticks))
    
    return model_price_ticks


def should_block_yes_buy(market_state: MarketState) -> bool:
    """
    Check if YES buys should be blocked based on oracle filter.
    
    Rule: Block YES if Model < ORACLE_BLOCK_YES (400 ticks = $0.40)
    
    Args:
        market_state: Current market state
    
    Returns:
        True if YES buys should be blocked, False otherwise
    """
    model_price_ticks = calculate_model_price(market_state)
    if model_price_ticks is None:
        # If we can't calculate model, don't block (conservative approach)
        return False
    
    # Block if model is too low (BTC is well below strike)
    # ORACLE_BLOCK_YES is 0.40, which is 400 ticks
    block_threshold = config.ORACLE_BLOCK_YES * 1000
    return model_price_ticks < block_threshold


def should_block_no_buy(market_state: MarketState) -> bool:
    """
    Check if NO buys should be blocked based on oracle filter.
    
    Rule: Block NO if Model > ORACLE_BLOCK_NO (600 ticks = $0.60)
    
    Args:
        market_state: Current market state
    
    Returns:
        True if NO buys should be blocked, False otherwise
    """
    model_price_ticks = calculate_model_price(market_state)
    if model_price_ticks is None:
        # If we can't calculate model, don't block (conservative approach)
        return False
    
    # Block if model is too high (BTC is well above strike)
    # ORACLE_BLOCK_NO is 0.60, which is 600 ticks
    block_threshold = config.ORACLE_BLOCK_NO * 1000
    return model_price_ticks > block_threshold

