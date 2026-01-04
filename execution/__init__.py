"""
Execution layer for order management and trade execution.
"""
from execution.order_state import OrderState, OrderStatus
from execution.execution_engine import ExecutionEngine
from execution.simulator import SimulatedExecutor
from execution.polymarket_api import PolymarketAPIClient

__all__ = [
    "OrderState",
    "OrderStatus",
    "ExecutionEngine",
    "SimulatedExecutor",
    "PolymarketAPIClient",
]


def create_execution_engine(mode: str = "simulated", **kwargs):
    """
    Factory function to create execution engine.
    
    Args:
        mode: "simulated" or "real"
        **kwargs: Additional arguments for execution engine
    
    Returns:
        ExecutionEngine instance
    """
    if mode == "simulated":
        return ExecutionEngine(mode="simulated", **kwargs)
    elif mode == "real":
        return ExecutionEngine(mode="real", **kwargs)
    else:
        raise ValueError(f"Unknown execution mode: {mode}")

