"""
Strategy stage functions for different priority levels.
Each function implements a specific trading stage/priority.
"""
from typing import Optional, List
from state.market_state import MarketState
from state.position_state import PositionState
from strategy.signals import TradeSignal
from strategy.oracle import should_block_yes_buy, should_block_no_buy
import config


def check_synthetic_arbitrage(
    market_state: MarketState,
    position_state: PositionState
) -> Optional[List[TradeSignal]]:
    """
    Priority 0: Synthetic Arbitrage Detection
    
    Checks if the market guarantees a profit right now by buying both sides.
    Logic: Best_Ask_YES + Best_Ask_NO < 1000 ticks
    
    If arbitrage exists:
    - Returns signals to buy both YES and NO simultaneously
    - Size: min(Ask_Size_YES, Ask_Size_NO, MAX_TRADE)
    
    Args:
        market_state: Current market state
        position_state: Current position state
    
    Returns:
        List of TradeSignals (both YES and NO), or None if no arbitrage
    """
    # Need both order books to be synced
    if not market_state.sync_status:
        return None
    
    # Get best ask prices
    best_ask_yes = market_state.get_best_ask_yes()
    best_ask_no = market_state.get_best_ask_no()
    
    # Need both prices to exist
    if best_ask_yes is None or best_ask_no is None:
        return None
    
    # Check if arbitrage exists: YES + NO < 1000 ticks
    total_cost = best_ask_yes + best_ask_no
    if total_cost >= 1000:
        return None  # No arbitrage opportunity
    
    # Don't generate arbitrage signals if we already have pending orders for either side
    if position_state.pending_yes or position_state.pending_no:
        return None  # Already have pending orders, wait for them to fill
    
    # Don't generate arbitrage if we already have both sides (already locked in profit)
    if position_state.has_both_sides():
        return None  # Already have arbitrage position, don't buy more
    
    # Calculate guaranteed profit
    profit_ticks = 1000 - total_cost
    
    # Get available sizes
    ask_size_yes = market_state.get_best_ask_size_yes()
    ask_size_no = market_state.get_best_ask_size_no()
    
    # Calculate trade size: min of both sides and MAX_TRADE
    trade_size = min(ask_size_yes, ask_size_no, config.MAX_TRADE)
    
    if trade_size <= 0:
        return None  # No size available
    
    # Create signals for both sides
    signals = [
        TradeSignal(
            side="YES",
            price=best_ask_yes,
            size=trade_size,
            reason=f"Synthetic arbitrage: YES+NO={total_cost:.1f} ticks, profit={profit_ticks:.1f} ticks",
            priority=0
        ),
        TradeSignal(
            side="NO",
            price=best_ask_no,
            size=trade_size,
            reason=f"Synthetic arbitrage: YES+NO={total_cost:.1f} ticks, profit={profit_ticks:.1f} ticks",
            priority=0
        )
    ]
    
    return signals


def apply_oracle_filter(
    market_state: MarketState,
    signals: List[TradeSignal]
) -> List[TradeSignal]:
    """
    Priority 1: Oracle Filter
    
    Filters out signals that violate the oracle model price.
    Never trade against the underlying asset (BTC) price trend.
    
    Rules:
    - Block YES buys if Model < 0.40 (BTC well below strike)
    - Block NO buys if Model > 0.60 (BTC well above strike)
    - Priority 0 (Synthetic Arbitrage) signals bypass this filter (risk-free)
    
    Args:
        market_state: Current market state
        signals: List of trade signals to filter
    
    Returns:
        Filtered list of signals (only those that pass oracle filter)
    """
    if not signals:
        return []
    
    filtered_signals = []
    
    for signal in signals:
        # Priority 0 (Synthetic Arbitrage) bypasses oracle filter
        # It's risk-free, so we don't block it
        if signal.priority == 0:
            filtered_signals.append(signal)
            continue
        
        # Apply oracle filter to directional trades
        if signal.side == "YES":
            if should_block_yes_buy(market_state):
                # Signal blocked by oracle filter
                continue
        elif signal.side == "NO":
            if should_block_no_buy(market_state):
                # Signal blocked by oracle filter
                continue
        
        # Signal passed oracle filter
        filtered_signals.append(signal)
    
    return filtered_signals


def check_bootstrap_stage(
    market_state: MarketState,
    position_state: PositionState
) -> Optional[TradeSignal]:
    """
    Priority 2: Bootstrap Stage (Legging In)
    
    Active when position is empty (Qy=0 and Qn=0).
    Buys the cheaper side when price is below threshold based on time remaining.
    
    Time Zone Rules:
    - T > 5m: Buy if Price < 0.40 (400 ticks)
    - 2m < T < 5m: Buy if Price < 0.15 (150 ticks)
    - T < 2m: NO ENTRY (kill zone)
    
    Execution:
    - Buy the cheaper side (YES or NO)
    - Size: min(Ask_Size, MAX_TRADE)
    
    Args:
        market_state: Current market state
        position_state: Current position state
    
    Returns:
        TradeSignal for the cheaper side, or None if conditions not met
    """
    # Only active when position is empty
    if not position_state.is_empty():
        return None
    
    # Check MAX_SHARES limit - don't bootstrap if already at limit
    if position_state.Qy >= config.MAX_SHARES or position_state.Qn >= config.MAX_SHARES:
        return None  # Already at max exposure, don't bootstrap
    
    # Need both order books to be synced
    if not market_state.sync_status:
        return None
    
    # Get time remaining
    time_remaining_minutes = market_state.get_time_remaining_minutes()
    if time_remaining_minutes is None:
        return None
    
    # Kill zone: No entry if T < 2m
    if time_remaining_minutes < config.BOOTSTRAP_KILL_ZONE:
        return None
    
    # Determine threshold based on time zone
    if time_remaining_minutes > config.BOOTSTRAP_HIGH_VOL_ZONE:
        # T > 5m: Use high threshold
        price_threshold = config.BOOTSTRAP_THRESHOLD_HIGH * 1000  # 400 ticks
    elif time_remaining_minutes > config.BOOTSTRAP_KILL_ZONE:
        # 2m < T < 5m: Use low threshold
        price_threshold = config.BOOTSTRAP_THRESHOLD_LOW * 1000  # 150 ticks
    else:
        # Should not reach here (already checked kill zone)
        return None
    
    # Get best ask prices
    best_ask_yes = market_state.get_best_ask_yes()
    best_ask_no = market_state.get_best_ask_no()
    
    # Need both prices to exist
    if best_ask_yes is None or best_ask_no is None:
        return None
    
    # Find the cheaper side
    if best_ask_yes <= best_ask_no:
        # YES is cheaper or equal
        cheaper_side = "YES"
        cheaper_price = best_ask_yes
        cheaper_size = market_state.get_best_ask_size_yes()
    else:
        # NO is cheaper
        cheaper_side = "NO"
        cheaper_price = best_ask_no
        cheaper_size = market_state.get_best_ask_size_no()
    
    # Don't generate signal if we already have a pending order for this side
    if (cheaper_side == "YES" and position_state.pending_yes) or \
       (cheaper_side == "NO" and position_state.pending_no):
        return None  # Already have pending order for this side
    
    # Check if price is below threshold
    if cheaper_price >= price_threshold:
        return None  # Price too high for entry
    
    # Check if size is available
    if cheaper_size <= 0:
        return None
    
    # Calculate trade size
    trade_size = min(cheaper_size, config.MAX_TRADE)
    
    # Create signal
    signal = TradeSignal(
        side=cheaper_side,
        price=cheaper_price,
        size=trade_size,
        reason=f"Bootstrap: {cheaper_side} at {cheaper_price:.1f} ticks (threshold: {price_threshold:.1f}, T={time_remaining_minutes:.1f}m)",
        priority=2
    )
    
    return signal


def check_hedging_stage(
    market_state: MarketState,
    position_state: PositionState
) -> Optional[TradeSignal]:
    """
    Priority 2: Hedging Stage (Rebalancing)
    
    Active when imbalance > BALANCE_PAD (abs(Qy - Qn) > 10).
    Goal: Neutralize risk immediately by buying the "light" side.
    
    Logic:
    - Identify which side is "heavy" (more shares) and "light" (fewer shares)
    - Calculate price limit: TARGET_PAIR - Avg_Cost_Heavy
    - Buy light side if Price < price_limit
    - If price is too high, hold and wait (unless Stop Loss triggers)
    
    Args:
        market_state: Current market state
        position_state: Current position state
    
    Returns:
        TradeSignal to buy the light side, or None if conditions not met
    """
    # Check if imbalance exceeds threshold
    imbalance = position_state.get_imbalance()
    if imbalance <= config.BALANCE_PAD:
        return None  # Position is balanced enough
    
    # Check MAX_SHARES limit - but allow hedging even if at limit (to reduce risk)
    # If both sides are at MAX_SHARES, we can't hedge more
    if position_state.Qy >= config.MAX_SHARES and position_state.Qn >= config.MAX_SHARES:
        return None  # Both sides at max, can't hedge
    
    # Need both order books to be synced
    if not market_state.sync_status:
        return None
    
    # Determine which side is heavy and which is light
    if position_state.Qy > position_state.Qn:
        # YES is heavy, NO is light
        heavy_side = "YES"
        light_side = "NO"
        heavy_qty = position_state.Qy
        heavy_avg_cost = position_state.get_avg_y_ticks()
        light_ask_price = market_state.get_best_ask_no()
        light_ask_size = market_state.get_best_ask_size_no()
    elif position_state.Qn > position_state.Qy:
        # NO is heavy, YES is light
        heavy_side = "NO"
        light_side = "YES"
        heavy_qty = position_state.Qn
        heavy_avg_cost = position_state.get_avg_n_ticks()
        light_ask_price = market_state.get_best_ask_yes()
        light_ask_size = market_state.get_best_ask_size_yes()
    else:
        # Should not reach here (imbalance check should catch this)
        return None
    
    # Need average cost of heavy side to calculate price limit
    if heavy_avg_cost is None or heavy_qty <= 0:
        return None
    
    # Need ask price for light side
    if light_ask_price is None:
        return None
    
    # Don't generate signal if we already have a pending order for the light side
    if (light_side == "YES" and position_state.pending_yes) or \
       (light_side == "NO" and position_state.pending_no):
        return None  # Already have pending order for light side
    
    # Calculate price limit: TARGET_PAIR - Avg_Cost_Heavy
    # This ensures we can lock in a profit when we complete the pair
    price_limit = config.TARGET_PAIR - heavy_avg_cost
    
    # Check if price is acceptable (below limit)
    if light_ask_price >= price_limit:
        # Price too high to lock profit - hold and wait
        # (Stop Loss will handle emergency exits separately)
        return None
    
    # Check if size is available
    if light_ask_size <= 0:
        return None
    
    # Calculate trade size
    # We want to buy enough to reduce imbalance, but not exceed MAX_TRADE or MAX_SHARES
    # Check how much we can buy without exceeding MAX_SHARES on the light side
    current_light_qty = position_state.Qy if light_side == "YES" else position_state.Qn
    max_allowed = max(0, config.MAX_SHARES - current_light_qty)
    
    if max_allowed <= 0:
        return None  # Already at MAX_SHARES on light side
    
    trade_size = min(light_ask_size, config.MAX_TRADE, max_allowed)
    
    # Create signal
    signal = TradeSignal(
        side=light_side,
        price=light_ask_price,
        size=trade_size,
        reason=f"Hedging: Buy {light_side} at {light_ask_price:.1f} ticks "
               f"(heavy: {heavy_side} {heavy_qty:.1f} @ {heavy_avg_cost:.1f}, "
               f"limit: {price_limit:.1f}, imbalance: {imbalance:.1f})",
        priority=2
    )
    
    return signal


def check_averaging_down_stage(
    market_state: MarketState,
    position_state: PositionState
) -> Optional[TradeSignal]:
    """
    Priority 2: Averaging Down Stage
    
    Active when we have a position and market moves against us.
    Reduces average cost by buying more at a lower price.
    
    Constraints:
    1. Floor Threshold: If Price < FLOOR_THRESH (200 ticks), STOP BUYING.
       A crash this deep usually means the outcome is decided.
    2. Balance Cap: Limit = max(0, (Q_other + BALANCE_PAD) - Q_this)
       Prevents creating too much imbalance. If we have 50 YES and 0 NO,
       we cannot buy more YES (Limit = -40). We must buy NO instead.
    
    Args:
        market_state: Current market state
        position_state: Current position state
    
    Returns:
        TradeSignal to average down, or None if conditions not met
    """
    # Need both order books to be synced
    if not market_state.sync_status:
        return None
    
    # Check MAX_SHARES limit - don't average down if at limit
    if position_state.Qy >= config.MAX_SHARES or position_state.Qn >= config.MAX_SHARES:
        return None
    
    # Check if we have a position on at least one side
    has_yes = position_state.Qy > 0
    has_no = position_state.Qn > 0
    
    if not has_yes and not has_no:
        return None  # No position to average down
    
    # Check YES side for averaging down opportunity
    if has_yes:
        avg_cost_yes = position_state.get_avg_y_ticks()
        if avg_cost_yes is not None:
            current_price_yes = market_state.get_best_ask_yes()
            if current_price_yes is not None:
                # Check if price moved against us (current < average)
                if current_price_yes < avg_cost_yes:
                    # Constraint 1: Floor threshold
                    floor_ticks = config.FLOOR_THRESH * 1000  # 200 ticks
                    if current_price_yes < floor_ticks:
                        # Price crashed too deep - don't average down
                        return None
                    
                    # Constraint 2: Balance cap
                    # Limit = max(0, (Q_no + BALANCE_PAD) - Q_yes)
                    balance_limit = max(0, (position_state.Qn + config.BALANCE_PAD) - position_state.Qy)
                    
                    # Check if we can buy more YES without exceeding imbalance
                    if balance_limit > 0:
                        # Don't generate signal if we already have a pending order for YES
                        if position_state.pending_yes:
                            return None  # Already have pending order for YES
                        
                        ask_size_yes = market_state.get_best_ask_size_yes()
                        if ask_size_yes > 0:
                            # Calculate trade size (limited by balance cap and MAX_TRADE)
                            trade_size = min(ask_size_yes, balance_limit, config.MAX_TRADE)
                            
                            if trade_size > 0:
                                signal = TradeSignal(
                                    side="YES",
                                    price=current_price_yes,
                                    size=trade_size,
                                    reason=f"Averaging down YES: {current_price_yes:.1f} < {avg_cost_yes:.1f} "
                                           f"(Qy={position_state.Qy:.1f}, Qn={position_state.Qn:.1f}, "
                                           f"limit={balance_limit:.1f})",
                                    priority=2
                                )
                                return signal
    
    # Check NO side for averaging down opportunity
    if has_no:
        avg_cost_no = position_state.get_avg_n_ticks()
        if avg_cost_no is not None:
            current_price_no = market_state.get_best_ask_no()
            if current_price_no is not None:
                # Check if price moved against us (current < average)
                if current_price_no < avg_cost_no:
                    # Constraint 1: Floor threshold
                    floor_ticks = config.FLOOR_THRESH * 1000  # 200 ticks
                    if current_price_no < floor_ticks:
                        # Price crashed too deep - don't average down
                        return None
                    
                    # Constraint 2: Balance cap
                    # Limit = max(0, (Q_yes + BALANCE_PAD) - Q_no)
                    balance_limit = max(0, (position_state.Qy + config.BALANCE_PAD) - position_state.Qn)
                    
                    # Check if we can buy more NO without exceeding imbalance
                    if balance_limit > 0:
                        # Don't generate signal if we already have a pending order for NO
                        if position_state.pending_no:
                            return None  # Already have pending order for NO
                        
                        ask_size_no = market_state.get_best_ask_size_no()
                        if ask_size_no > 0:
                            # Calculate trade size (limited by balance cap and MAX_TRADE)
                            trade_size = min(ask_size_no, balance_limit, config.MAX_TRADE)
                            
                            if trade_size > 0:
                                signal = TradeSignal(
                                    side="NO",
                                    price=current_price_no,
                                    size=trade_size,
                                    reason=f"Averaging down NO: {current_price_no:.1f} < {avg_cost_no:.1f} "
                                           f"(Qy={position_state.Qy:.1f}, Qn={position_state.Qn:.1f}, "
                                           f"limit={balance_limit:.1f})",
                                    priority=2
                                )
                                return signal
    
    # No averaging down opportunity
    return None


def check_profit_lock(
    market_state: MarketState,
    position_state: PositionState
) -> bool:
    """
    Priority 3: Profit Lock Check
    
    Calculates if we've locked in a profit and should halt all trading.
    
    Profit Formula:
        Profit = (min(Q_y, Q_n) × 1000) - (C_y + C_n) + (|Q_y - Q_n| × P_bid_excess)
    
    Where:
        - min(Q_y, Q_n) × 1000: Value of complete pairs (each pair pays 1000 ticks)
        - C_y + C_n: Total cost basis
        - |Q_y - Q_n| × P_bid_excess: Value of excess shares at current bid price
    
    If Profit > 0 → HALT ALL TRADING. The window is won.
    
    Args:
        market_state: Current market state
        position_state: Current position state
    
    Returns:
        True if profit is locked (should halt trading), False otherwise
    """
    # Need both order books to be synced
    if not market_state.sync_status:
        return False
    
    # Need to have both sides to calculate profit
    if not position_state.has_both_sides():
        return False  # Can't lock profit without both sides
    
    # Get quantities
    qy = position_state.Qy
    qn = position_state.Qn
    
    # Calculate value of complete pairs
    complete_pairs = min(qy, qn)
    pair_value = complete_pairs * config.PAYOUT_TICKS  # 1000 ticks per pair
    
    # Total cost basis
    total_cost = position_state.Cy + position_state.Cn
    
    # Calculate excess shares value
    excess = abs(qy - qn)
    excess_value = 0.0
    
    if excess > 0:
        # Get bid price for excess side
        if qy > qn:
            # Excess YES shares
            best_bid_yes = market_state.get_best_bid_yes()
            if best_bid_yes is not None:
                excess_value = excess * best_bid_yes
        else:
            # Excess NO shares
            best_bid_no = market_state.get_best_bid_no()
            if best_bid_no is not None:
                excess_value = excess * best_bid_no
    
    # Calculate total profit
    profit = pair_value - total_cost + excess_value
    
    # Profit is locked if > 0
    return profit > 0


def check_stop_loss(
    market_state: MarketState,
    position_state: PositionState
) -> bool:
    """
    Priority 3: Stop Loss Check
    
    If we have a solo position (only YES or only NO) and the best bid price
    drops below STOP_LOSS (250 ticks = $0.25), we should panic-sell.
    
    Args:
        market_state: Current market state
        position_state: Current position state
    
    Returns:
        True if stop loss is triggered (should sell), False otherwise
    """
    # Need both order books to be synced
    if not market_state.sync_status:
        return False
    
    stop_loss_ticks = config.STOP_LOSS * 1000  # 250 ticks
    
    # Check if we have only YES shares
    if position_state.has_only_yes():
        best_bid_yes = market_state.get_best_bid_yes()
        if best_bid_yes is not None and best_bid_yes < stop_loss_ticks:
            # Stop loss triggered for YES
            return True
    
    # Check if we have only NO shares
    if position_state.has_only_no():
        best_bid_no = market_state.get_best_bid_no()
        if best_bid_no is not None and best_bid_no < stop_loss_ticks:
            # Stop loss triggered for NO
            return True
    
    # Stop loss not triggered
    return False

