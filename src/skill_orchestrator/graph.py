"""Dependency graph - DAG structure for skill orchestration."""

from typing import Optional, Union

from skill_orchestrator.models import SkillNode, SkillType, NodeStatus, ExecutionPhase


class DependencyGraph:
    """DAG for skill execution planning.

    Features:
    - Add nodes with dependencies
    - Detect cycles
    - Topological sort
    - Generate parallel execution phases
    - Track execution status
    """

    def __init__(self):
        self.nodes: dict[str, SkillNode] = {}
        self._adjacency: dict[str, set[str]] = {}  # node -> dependents
        self._reverse_adj: dict[str, set[str]] = {}  # node -> dependencies

    def add_node(self, node: SkillNode) -> None:
        """Add a skill node to the graph."""
        node_id = node.id
        self.nodes[node_id] = node

        if node_id not in self._adjacency:
            self._adjacency[node_id] = set()
        if node_id not in self._reverse_adj:
            self._reverse_adj[node_id] = set()

        # Add edges for dependencies
        for dep in node.depends_on:
            if dep not in self._adjacency:
                self._adjacency[dep] = set()
            self._adjacency[dep].add(node_id)
            self._reverse_adj[node_id].add(dep)

    def remove_node(self, node_id: str) -> bool:
        """Remove a node from the graph."""
        if node_id not in self.nodes:
            return False

        for dep in self._reverse_adj.get(node_id, set()).copy():
            self._adjacency[dep].discard(node_id)

        for dependent in self._adjacency.get(node_id, set()).copy():
            self._reverse_adj[dependent].discard(node_id)

        del self.nodes[node_id]
        self._adjacency.pop(node_id, None)
        self._reverse_adj.pop(node_id, None)
        return True

    def get_node(self, node_id: str) -> Optional[SkillNode]:
        """Get a node by ID."""
        return self.nodes.get(node_id)

    def detect_cycle(self) -> Optional[list[str]]:
        """Detect if graph has a cycle using DFS."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {node: WHITE for node in self.nodes}
        parent: dict[str, str] = {}

        def dfs(node: str) -> Optional[list[str]]:
            color[node] = GRAY
            for neighbor in self._adjacency.get(node, []):
                if neighbor not in color:
                    continue
                if color[neighbor] == GRAY:
                    cycle = [neighbor, node]
                    current = node
                    while current in parent and parent[current] != neighbor:
                        current = parent[current]
                        cycle.append(current)
                    return cycle[::-1]
                if color[neighbor] == WHITE:
                    parent[neighbor] = node
                    result = dfs(neighbor)
                    if result:
                        return result
            color[node] = BLACK
            return None

        for node in self.nodes:
            if color[node] == WHITE:
                result = dfs(node)
                if result:
                    return result
        return None

    def topological_sort(self) -> list[str]:
        """Return nodes in topological order (dependencies first)."""
        cycle = self.detect_cycle()
        if cycle:
            raise ValueError(f"Cycle detected: {' -> '.join(cycle)}")

        in_degree = {
            node: len(self._reverse_adj.get(node, set())) for node in self.nodes
        }
        queue = [node for node, degree in in_degree.items() if degree == 0]
        result = []

        while queue:
            queue.sort()
            node = queue.pop(0)
            result.append(node)
            for neighbor in self._adjacency.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return result

    def get_execution_phases(self) -> list[ExecutionPhase]:
        """Group nodes into parallel execution phases."""
        levels: dict[str, int] = {}

        def get_level(node: str) -> int:
            if node in levels:
                return levels[node]
            deps = self._reverse_adj.get(node, set())
            if not deps:
                levels[node] = 0
            else:
                valid_deps = [d for d in deps if d in self.nodes]
                levels[node] = max(get_level(d) for d in valid_deps) + 1 if valid_deps else 0
            return levels[node]

        for node in self.nodes:
            get_level(node)

        phase_map: dict[int, list[str]] = {}
        for node, level in levels.items():
            phase_map.setdefault(level, []).append(node)

        phases = []
        for phase_num in sorted(phase_map.keys()):
            nodes = sorted(phase_map[phase_num])
            mode = "parallel" if len(nodes) > 1 else "sequential"
            phases.append(ExecutionPhase(phase_number=phase_num + 1, nodes=nodes, mode=mode))

        return phases

    def get_ready_nodes(self) -> list[str]:
        """Get nodes that are ready to execute."""
        ready = []
        completed_statuses = {NodeStatus.COMPLETED}

        for node_id, node in self.nodes.items():
            if node.status != NodeStatus.PENDING:
                continue
            deps = self._reverse_adj.get(node_id, set())
            if all(
                self.nodes[d].status in completed_statuses
                for d in deps
                if d in self.nodes
            ):
                ready.append(node_id)

        return sorted(ready)

    def update_status(
        self,
        node_id: str,
        status: Union[NodeStatus, str],
        output_path: Optional[str] = None,
    ) -> None:
        """Update node status.

        Args:
            node_id: Node ID to update
            status: New status (NodeStatus enum or string like "running", "completed")
            output_path: Optional output path for completed nodes
        """
        if node_id not in self.nodes:
            return

        # Convert string to NodeStatus if needed
        if isinstance(status, str):
            status = NodeStatus(status)

        node = self.nodes[node_id]
        updated = node.model_copy(
            update={"status": status, "output_path": output_path or node.output_path}
        )
        self.nodes[node_id] = updated

    def fail_node(self, node_id: str) -> None:
        """Mark a node as failed and cascade skip to all dependents."""
        if node_id not in self.nodes:
            return

        self.update_status(node_id, NodeStatus.FAILED)

        # Skip all dependents recursively
        def skip_dependents(nid: str) -> None:
            for dep in self._adjacency.get(nid, []):
                if dep in self.nodes and self.nodes[dep].status == NodeStatus.PENDING:
                    self.update_status(dep, NodeStatus.SKIPPED)
                    skip_dependents(dep)

        skip_dependents(node_id)

    def get_dependents(self, node_id: str) -> list[str]:
        """Get all nodes that depend on the given node."""
        return list(self._adjacency.get(node_id, set()))

    def get_dependencies(self, node_id: str) -> list[str]:
        """Get all nodes that the given node depends on."""
        return list(self._reverse_adj.get(node_id, set()))

    def is_complete(self) -> bool:
        """Check if all nodes are in a terminal state."""
        return all(node.is_terminal for node in self.nodes.values())

    def get_stats(self) -> dict:
        """Get execution statistics."""
        stats = {
            "total": len(self.nodes),
            "pending": 0,
            "running": 0,
            "completed": 0,
            "failed": 0,
            "skipped": 0,
            "primary": 0,
            "helper": 0,
        }

        for node in self.nodes.values():
            stats[node.status.value] += 1
            stats[node.skill_type.value] += 1

        return stats

    def to_dict(self) -> dict:
        """Export graph to dictionary."""
        return {
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "phases": [p.to_dict() for p in self.get_execution_phases()],
        }

    def __len__(self) -> int:
        return len(self.nodes)

    def __contains__(self, node_id: str) -> bool:
        return node_id in self.nodes

    def __repr__(self) -> str:
        return f"DependencyGraph(nodes={len(self.nodes)}, phases={len(self.get_execution_phases())})"


def build_graph_from_nodes(nodes_data: list[dict]) -> DependencyGraph:
    """Build a dependency graph from a list of node dictionaries."""
    graph = DependencyGraph()

    for node_data in nodes_data:
        skill_type = (
            SkillType.PRIMARY if node_data.get("type") == "primary" else SkillType.HELPER
        )
        node = SkillNode(
            id=node_data.get("id", node_data.get("name", "")),
            name=node_data.get("name", ""),
            skill_type=skill_type,
            depends_on=node_data.get("depends_on", []),
            purpose=node_data.get("purpose", ""),
            # Collaboration fields
            outputs_summary=node_data.get("outputs_summary", ""),
            downstream_hint=node_data.get("downstream_hint", ""),
            usage_hints=node_data.get("usage_hints", {}),
        )
        graph.add_node(node)

    return graph
