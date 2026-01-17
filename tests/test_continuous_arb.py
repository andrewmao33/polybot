import unittest
import sys
import os
sys.path.append(os.getcwd()) # Add current dir to path

from state.position_state import PositionState
from state.market_state import MarketState
from strategy.continuous_arb import calculate_target_orders
import config

class TestContinuousArb(unittest.TestCase):
    def setUp(self):
        self.market = MarketState("test_market", 100000, 0)
        self.position = PositionState("test_market")
        
        # Reset config for testing
        config.MAX_TRADE = 30
        config.BALANCE_PAD = 300

    def test_grow_and_balance_logic(self):
        """Test the 'Grow & Balance' sizing logic."""
        # Scenario: Imbalance of 10 YES (10 YES, 0 NO)
        self.position.Qy = 10
        self.position.Qn = 0
        
        targets = calculate_target_orders(self.position, self.market)
        
        # Expect:
        # NO target = MAX (30)
        # YES target = MAX - 10 = 20
        
        yes_target = next(t for t in targets if t['side'] == 'YES')
        no_target = next(t for t in targets if t['side'] == 'NO')
        
        self.assertEqual(no_target['size'], 30.0)
        self.assertEqual(yes_target['size'], 20.0)
        
    def test_heavy_imbalance(self):
        """Test what happens if Imbalance > MAX_TRADE."""
        # Scenario: Imbalance of 50 YES (50 YES, 0 NO)
        self.position.Qy = 50
        self.position.Qn = 0
        
        targets = calculate_target_orders(self.position, self.market)
        
        # Expect:
        # NO target = 30
        # YES target = 0 (30 - 50 is negative, clipped to 0)
        
        no_target = next(t for t in targets if t['side'] == 'NO')
        # YES target should not exist or be 0
        yes_targets = [t for t in targets if t['side'] == 'YES']
        
        self.assertEqual(no_target['size'], 30.0)
        self.assertEqual(len(yes_targets), 0)

if __name__ == '__main__':
    unittest.main()
