"""Headless visualizer for batch execution."""

from typing import Optional


class NullVisualizer:
    """No-UI visualizer for headless mode"""

    def __init__(self, auto_select_plan: int = 0):
        self.auto_select_plan = auto_select_plan
        self.logs = []
        # Metrics aggregation
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0

    async def start(self) -> None:
        """No-op for headless mode."""
        pass

    async def stop(self) -> None:
        """No-op for headless mode."""
        pass

    async def set_task(self, task: str) -> None:
        pass

    async def set_nodes(self, nodes: list, phases: list) -> None:
        pass

    async def update_status(self, node_id: str, status: str) -> None:
        pass

    async def set_phase(self, phase_num: int) -> None:
        pass

    async def add_log(self, message: str, level: str = "info", node_id: Optional[str] = None) -> None:
        self.logs.append({"message": message, "level": level, "node_id": node_id})
        if level == "error":
            print(f"[ERROR] {message}")

    def add_metrics(self, input_tokens: int, output_tokens: int, cost: float) -> None:
        """Accumulate metrics"""
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_cost += cost

    async def select_plan(self, plans: list) -> int:
        return min(self.auto_select_plan, len(plans) - 1)
