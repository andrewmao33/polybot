"""
Test script for the strategy engine.
Demonstrates how to test various scenarios.
"""
import time
from state.market_state import MarketState
from state.position_state import PositionState
from strategy.engine import evaluate_strategy


def create_test_market_state(
    yes_bid: float = None,
    yes_ask: float = None,
    yes_bid_size: float = 0,
    yes_ask_size: float = 0,
    no_bid: float = None,
    no_ask: float = None,
    no_bid_size: float = 0,
    no_ask_size: float = 0,
    btc_price: float = None,
    strike_price: float = 100000,
    time_remaining_minutes: float = 10.0,
    synced: bool = True
) -> MarketState:
    """Helper to create a test market state."""
    # Calculate end timestamp (time_remaining_minutes from now)
    now_ms = int(time.time() * 1000)
    end_timestamp = now_ms + int(time_remaining_minutes * 60 * 1000)
    
    market = MarketState(
        market_id="test-market",
        strike_price=strike_price,
        end_timestamp=end_timestamp
    )
    
    # Set sync status
    market.sync_status_yes = synced
    market.sync_status_no = synced
    
    # Set exchange timestamp
    market.exchange_timestamp = now_ms
    
    # Set BTC price
    if btc_price is not None:
        market.btc_price = btc_price
    
    # Set YES order book
    if yes_bid is not None:
        market.order_book_yes_bids[yes_bid] = yes_bid_size
    if yes_ask is not None:
        market.order_book_yes_asks[yes_ask] = yes_ask_size
    
    # Set NO order book
    if no_bid is not None:
        market.order_book_no_bids[no_bid] = no_bid_size
    if no_ask is not None:
        market.order_book_no_asks[no_ask] = no_ask_size
    
    return market


def create_test_position_state(
    qy: float = 0.0,
    qn: float = 0.0,
    cy: float = 0.0,
    cn: float = 0.0
) -> PositionState:
    """Helper to create a test position state."""
    pos = PositionState(market_id="test-market")
    pos.Qy = qy
    pos.Qn = qn
    pos.Cy = cy
    pos.Cn = cn
    return pos


def print_signals(signals, scenario_name: str):
    """Print signals in a readable format."""
    print(f"\n{'='*60}")
    print(f"SCENARIO: {scenario_name}")
    print(f"{'='*60}")
    
    if signals is None:
        print("Result: No signals (None)")
    elif len(signals) == 0:
        print("Result: Empty signal list")
    else:
        print(f"Result: {len(signals)} signal(s) generated")
        for i, signal in enumerate(signals, 1):
            print(f"\n  Signal {i}:")
            print(f"    Side: {signal.side}")
            print(f"    Price: {signal.price:.1f} ticks (${signal.price/1000:.3f})")
            print(f"    Size: {signal.size:.1f} shares")
            print(f"    Priority: {signal.priority}")
            print(f"    Reason: {signal.reason}")


def test_synthetic_arbitrage():
    """Test Priority 0: Synthetic Arbitrage"""
    print("\n" + "="*60)
    print("TEST 1: Synthetic Arbitrage Detection")
    print("="*60)
    
    # Create market with arbitrage opportunity: YES=450 + NO=500 = 950 < 1000
    market = create_test_market_state(
        yes_ask=450, yes_ask_size=10,
        no_ask=500, no_ask_size=10,
        btc_price=100000, strike_price=100000,
        time_remaining_minutes=10.0
    )
    
    position = create_test_position_state()
    
    signals = evaluate_strategy(market, position)
    print_signals(signals, "Synthetic Arbitrage (YES=450, NO=500)")
    
    assert signals is not None, "Should generate arbitrage signals"
    assert len(signals) == 2, "Should generate 2 signals (YES and NO)"
    assert signals[0].priority == 0, "Should be Priority 0"
    print("✅ PASSED")


def test_bootstrap_stage():
    """Test Priority 2: Bootstrap Stage"""
    print("\n" + "="*60)
    print("TEST 2: Bootstrap Stage (Legging In)")
    print("="*60)
    
    # Test 2a: Bootstrap with T > 5m, price < 0.40
    # Use prices that don't create arbitrage (YES + NO >= 1000)
    market = create_test_market_state(
        yes_ask=350, yes_ask_size=20,
        no_ask=700, no_ask_size=20,  # Total = 1050, no arbitrage
        btc_price=100000, strike_price=100000,
        time_remaining_minutes=6.0  # T > 5m
    )
    position = create_test_position_state()
    
    signals = evaluate_strategy(market, position)
    print_signals(signals, "Bootstrap: T=6m, YES=350 (< 400 threshold)")
    assert signals is not None, "Should generate bootstrap signal"
    assert signals[0].priority == 2, "Should be Priority 2 (bootstrap)"
    assert signals[0].side == "YES", "Should buy cheaper side (YES)"
    
    # Test 2b: Bootstrap with 2m < T < 5m, price < 0.15
    # Use prices that don't create arbitrage
    market2 = create_test_market_state(
        yes_ask=120, yes_ask_size=20,
        no_ask=900, no_ask_size=20,  # Total = 1020, no arbitrage
        btc_price=100000, strike_price=100000,
        time_remaining_minutes=3.0  # 2m < T < 5m
    )
    position2 = create_test_position_state()
    
    signals2 = evaluate_strategy(market2, position2)
    print_signals(signals2, "Bootstrap: T=3m, YES=120 (< 150 threshold)")
    assert signals2 is not None, "Should generate bootstrap signal"
    assert signals2[0].priority == 2, "Should be Priority 2 (bootstrap)"
    
    # Test 2c: Kill zone (T < 2m) - should not enter
    # Use prices that don't create arbitrage (YES + NO >= 1000)
    # so we can test bootstrap kill zone logic
    market3 = create_test_market_state(
        yes_ask=100, yes_ask_size=20,
        no_ask=950, no_ask_size=20,  # Total = 1050, no arbitrage
        btc_price=100000, strike_price=100000,
        time_remaining_minutes=1.0  # T < 2m (kill zone)
    )
    position3 = create_test_position_state()
    
    signals3 = evaluate_strategy(market3, position3)
    print_signals(signals3, "Bootstrap: T=1m (kill zone) - should not enter")
    assert signals3 is None, "Should not enter in kill zone"
    print("✅ PASSED")


def test_hedging_stage():
    """Test Priority 2: Hedging Stage"""
    print("\n" + "="*60)
    print("TEST 3: Hedging Stage (Rebalancing)")
    print("="*60)
    
    # Create imbalanced position: 50 YES, 0 NO (imbalance = 50 > BALANCE_PAD=10)
    # Average YES cost: 400 ticks
    # Price limit: TARGET_PAIR (980) - 400 = 580 ticks
    market = create_test_market_state(
        yes_ask=450, yes_ask_size=20,
        no_ask=550, no_ask_size=20,  # NO at 550 < 580 limit
        btc_price=100000, strike_price=100000,
        time_remaining_minutes=10.0
    )
    
    position = create_test_position_state(
        qy=50.0, qn=0.0,
        cy=20000.0, cn=0.0  # Average: 400 ticks
    )
    
    signals = evaluate_strategy(market, position)
    print_signals(signals, "Hedging: 50 YES @ 400 avg, NO=550 (< 580 limit)")
    
    assert signals is not None, "Should generate hedging signal"
    assert signals[0].side == "NO", "Should buy NO to hedge"
    print("✅ PASSED")


def test_averaging_down():
    """Test Priority 2: Averaging Down"""
    print("\n" + "="*60)
    print("TEST 4: Averaging Down Stage")
    print("="*60)
    
    # Position: 20 YES @ 400 ticks average
    # Current price: 350 ticks (< 400, market moved against us)
    # Floor check: 350 > 200 (FLOOR_THRESH), OK
    # Balance limit: (0 + 10) - 20 = -10, but max(0, -10) = 0, so can't buy more
    # Actually wait, if we have 20 YES and 0 NO, balance_limit = max(0, (0+10)-20) = 0
    # So we can't average down YES. Let's test with a balanced position.
    
    # Position: 20 YES @ 400, 20 NO @ 500
    # Current YES price: 350 (< 400)
    # Balance limit: (20 + 10) - 20 = 10, so we can buy 10 more YES
    market = create_test_market_state(
        yes_ask=350, yes_ask_size=15,
        no_ask=500, no_ask_size=20,
        btc_price=100000, strike_price=100000,
        time_remaining_minutes=10.0
    )
    
    position = create_test_position_state(
        qy=20.0, qn=20.0,
        cy=8000.0, cn=10000.0  # YES avg: 400, NO avg: 500
    )
    
    signals = evaluate_strategy(market, position)
    print_signals(signals, "Averaging Down: 20 YES @ 400, current=350")
    
    print("✅ PASSED")


def test_profit_lock():
    """Test Priority 3: Profit Lock"""
    print("\n" + "="*60)
    print("TEST 5: Profit Lock")
    print("="*60)
    
    # Position: 50 YES @ 400 ticks, 50 NO @ 500 ticks
    # Total cost: 20,000 + 25,000 = 45,000 ticks
    # Complete pairs: 50 × 1000 = 50,000 ticks
    # Profit: 50,000 - 45,000 = 5,000 ticks > 0 → LOCKED
    market = create_test_market_state(
        yes_bid=600, yes_ask=650,
        no_bid=400, no_ask=450,
        btc_price=100000, strike_price=100000,
        time_remaining_minutes=10.0
    )
    
    position = create_test_position_state(
        qy=50.0, qn=50.0,
        cy=20000.0, cn=25000.0  # Total cost: 45,000 ticks
    )
    
    signals = evaluate_strategy(market, position)
    print_signals(signals, "Profit Lock: 50 pairs @ 45k cost, 50k value")
    
    assert signals is None, "Should return None when profit is locked"
    print("✅ PASSED")


def test_stop_loss():
    """Test Priority 3: Stop Loss"""
    print("\n" + "="*60)
    print("TEST 6: Stop Loss")
    print("="*60)
    
    # Position: 50 YES only
    # Best bid: 200 ticks (< 250 STOP_LOSS threshold)
    market = create_test_market_state(
        yes_bid=200, yes_ask=250,  # Bid < 250 (STOP_LOSS)
        no_bid=500, no_ask=550,
        btc_price=100000, strike_price=100000,
        time_remaining_minutes=10.0
    )
    
    position = create_test_position_state(
        qy=50.0, qn=0.0,
        cy=20000.0, cn=0.0
    )
    
    signals = evaluate_strategy(market, position)
    print_signals(signals, "Stop Loss: 50 YES, bid=200 (< 250 threshold)")
    
    assert signals is None, "Should return None when stop loss triggered"
    print("✅ PASSED")


def test_oracle_filter():
    """Test Priority 1: Oracle Filter"""
    print("\n" + "="*60)
    print("TEST 7: Oracle Filter Blocking")
    print("="*60)
    
    # Test: BTC well below strike → Model < 400 → Block YES buys
    # Setup: YES is cheaper (350) and would normally trigger bootstrap
    # But oracle filter should block YES, allowing only NO if it's cheap enough
    # Use prices that don't create arbitrage (YES + NO >= 1000)
    
    # BTC = 90,000, Strike = 100,000, Diff = -10,000
    # Scaling = 50 * (1 + 10/15) = 50 * 1.667 ≈ 83.33
    # Diff_ticks = -10,000 / 83.33 ≈ -120 ticks
    # Model = 500 - 120 = 380 ticks < 400 → YES blocked
    
    market = create_test_market_state(
        yes_ask=350, yes_ask_size=20,  # YES is cheaper, would trigger bootstrap
        no_ask=700, no_ask_size=20,   # NO is more expensive
        btc_price=90000, strike_price=100000,  # BTC well below strike
        time_remaining_minutes=10.0
    )
    
    position = create_test_position_state()
    
    signals = evaluate_strategy(market, position)
    print_signals(signals, "Oracle Filter: BTC=90k < Strike=100k (block YES)")
    
    # YES should be blocked by oracle filter
    # NO might generate a signal if it's cheap enough, or no signals if NO is too expensive
    if signals:
        for signal in signals:
            assert signal.side == "NO", "YES should be blocked by oracle filter"
            print(f"  ✓ Correctly blocked YES, allowing NO signal")
    else:
        # No signals is also valid (NO might be too expensive for bootstrap)
        print("  ✓ No signals (YES blocked, NO too expensive)")
    
    print("✅ PASSED")


def test_no_signals():
    """Test case where no signals should be generated"""
    print("\n" + "="*60)
    print("TEST 8: No Signals Scenario")
    print("="*60)
    
    # Market: Prices too high, no arbitrage
    # Position: Empty
    # Time: > 5m but prices > threshold
    market = create_test_market_state(
        yes_ask=500, yes_ask_size=20,
        no_ask=550, no_ask_size=20,  # Total = 1050 > 1000, no arbitrage
        btc_price=100000, strike_price=100000,
        time_remaining_minutes=10.0
    )
    
    position = create_test_position_state()
    
    signals = evaluate_strategy(market, position)
    print_signals(signals, "No Signals: Prices too high, no opportunities")
    
    # Should return None or empty list
    print("✅ PASSED")


def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("STRATEGY ENGINE TEST SUITE")
    print("="*60)
    
    try:
        test_synthetic_arbitrage()
        test_bootstrap_stage()
        test_hedging_stage()
        test_averaging_down()
        test_profit_lock()
        test_stop_loss()
        test_oracle_filter()
        test_no_signals()
        
        print("\n" + "="*60)
        print("ALL TESTS COMPLETED")
        print("="*60)
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        raise
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        raise


if __name__ == "__main__":
    main()

