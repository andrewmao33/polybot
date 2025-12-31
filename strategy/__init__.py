"""
Strategy engine for the trading bot.
Contains the core trading logic that evaluates market state and generates trade signals.
"""

from strategy.signals import TradeSignal
from strategy.engine import evaluate_strategy

__all__ = ['TradeSignal', 'evaluate_strategy']

