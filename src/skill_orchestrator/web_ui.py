"""Web UI components for skill orchestration.

This module provides core state and visualizer classes used by unified_service.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Set

from fastapi import WebSocket


@dataclass
class OrchestratorState:
    """Global state for orchestration, shared across WebSocket connections."""
    task: str = ""
    start_time: Optional[datetime] = None
    current_phase: int = 0
    nodes: list = field(default_factory=list)
    logs: list = field(default_factory=list)
    node_times: dict = field(default_factory=dict)
    is_running: bool = False
    result: Optional[dict] = None
    # Plan selection
    plans: list = field(default_factory=list)
    selected_plan_index: Optional[int] = None
    waiting_for_selection: bool = False

    def get_elapsed(self) -> str:
        """Get elapsed time string."""
        if not self.start_time:
            return "0:00"
        elapsed = datetime.now() - self.start_time
        minutes = int(elapsed.total_seconds() // 60)
        seconds = int(elapsed.total_seconds() % 60)
        return f"{minutes}:{seconds:02d}"

    def to_dict(self) -> dict:
        """Convert state to dict for sending to frontend."""
        return {
            "task": self.task,
            "elapsed": self.get_elapsed(),
            "current_phase": self.current_phase,
            "nodes": self.nodes,
            "logs": self.logs,
            "plans": self.plans,
            "waiting_for_selection": self.waiting_for_selection,
        }


class WebVisualizer:
    """WebSocket-based visualizer implementing VisualizerProtocol."""

    _flush_interval = 0.05  # 50ms batch interval

    def __init__(self, state: OrchestratorState, clients: Set[WebSocket]):
        self.state = state
        self.clients = clients
        # Log buffering for batch sending
        self._log_buffer: list[dict] = []
        self._log_lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        """Start the log flush background task."""
        if self._running:
            return
        self._running = True
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        """Stop the log flush task and flush remaining logs."""
        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None
        # Final flush
        await self._flush_logs()

    async def _flush_loop(self) -> None:
        """Periodically flush buffered logs."""
        while self._running:
            await asyncio.sleep(self._flush_interval)
            await self._flush_logs()

    async def _flush_logs(self) -> None:
        """Send buffered logs as a batch."""
        async with self._log_lock:
            if not self._log_buffer:
                return
            batch = self._log_buffer[:]
            self._log_buffer.clear()
        # Broadcast batch
        if batch:
            await self._broadcast("log_batch", batch)

    async def _broadcast(self, msg_type: str, data) -> None:
        """Broadcast message to all connected clients."""
        message = {"type": msg_type, "data": data}
        disconnected = set()
        for client in list(self.clients):
            try:
                await client.send_json(message)
            except Exception:
                disconnected.add(client)
        # Remove disconnected clients
        self.clients -= disconnected

    async def set_task(self, task: str) -> None:
        """Set the current task description."""
        self.state.task = task
        self.state.start_time = datetime.now()
        self.state.is_running = True
        await self._broadcast("task", task)

    async def set_nodes(self, nodes: list[dict], phases: list) -> None:
        """Initialize with node list and phases."""
        node_data = [
            {
                "id": n["id"],
                "name": n.get("name", n["id"]),
                "purpose": n.get("purpose", ""),
                "depends_on": n.get("depends_on", []),
                "status": "pending"
            }
            for n in nodes
        ]
        self.state.nodes = node_data
        await self._broadcast("nodes", {"nodes": node_data})

    async def update_status(self, node_id: str, status: str) -> None:
        """Update node status."""
        if status == "running" and node_id not in self.state.node_times:
            self.state.node_times[node_id] = datetime.now()

        time_str = ""
        if node_id in self.state.node_times:
            elapsed = (datetime.now() - self.state.node_times[node_id]).total_seconds()
            time_str = f"{elapsed:.1f}s"

        # Update state
        for node in self.state.nodes:
            if node["id"] == node_id:
                node["status"] = status
                if time_str:
                    node["time"] = time_str
                break

        await self._broadcast("status", {
            "node_id": node_id,
            "status": status,
            "time": time_str
        })

    async def set_phase(self, phase_num: int) -> None:
        """Set current phase number."""
        self.state.current_phase = phase_num
        await self._broadcast("phase", phase_num)

    async def add_log(self, message: str, level: str = "info", node_id: Optional[str] = None) -> None:
        """Add log entry with optional node context.

        Args:
            message: The log message
            level: Log level (info, tool, send, recv, ok, error, warn)
            node_id: Optional node ID for sub-agent logs (None for main agent)
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        elapsed = self.state.get_elapsed()

        icons = {
            "info": "💬", "tool": "🔧", "send": "📤", "recv": "📥",
            "ok": "✅", "error": "❌", "warn": "⚠️"
        }

        log_entry = {
            "message": message,
            "level": level,
            "timestamp": timestamp,
            "elapsed": elapsed,
            "icon": icons.get(level, "•"),
            "node_id": node_id,
        }
        self.state.logs.append(log_entry)
        # Buffer log for batch sending
        async with self._log_lock:
            self._log_buffer.append(log_entry)

    async def select_plan(self, plans: list) -> int:
        """Send plans to UI and wait for user selection.

        Args:
            plans: List of plan dicts with name, description, nodes

        Returns:
            Index of selected plan
        """
        self.state.plans = plans
        self.state.waiting_for_selection = True
        self.state.selected_plan_index = None
        await self._broadcast("plans", {"plans": plans})

        # Wait for user selection
        while self.state.selected_plan_index is None:
            await asyncio.sleep(0.1)

        self.state.waiting_for_selection = False
        return self.state.selected_plan_index
