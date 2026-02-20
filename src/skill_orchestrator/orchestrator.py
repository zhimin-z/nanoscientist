"""Skill Orchestrator - coordinates multiple skills using ClaudeSDKClient."""

import asyncio
import json
import re
import time
import traceback
from pathlib import Path
from typing import Optional, Protocol

from config import get_config
from skill_orchestrator.client import SkillClient
from skill_orchestrator.models import (
    SkillMetadata,
    NodeStatus,
    NodeFailureReason,
    NodeExecutionResult,
    ExecutionPhase,
)
from skill_orchestrator.skills import SkillRegistry
from skill_orchestrator.graph import DependencyGraph, build_graph_from_nodes
from skill_orchestrator.prompts import (
    build_planner_prompt,
    build_executor_prompt,
    build_isolated_executor_prompt,
    build_direct_executor_prompt,
)
from skill_orchestrator.throttler import ExecutionThrottler
from skill_orchestrator.run_context import RunContext


class VisualizerProtocol(Protocol):
    """Protocol for visualizer implementations."""
    async def set_task(self, task: str) -> None: ...
    async def set_nodes(self, nodes: list[dict], phases: list) -> None: ...
    async def update_status(self, node_id: str, status: str) -> None: ...
    async def set_phase(self, phase_num: int) -> None: ...
    async def add_log(self, message: str, level: str = "info", node_id: Optional[str] = None) -> None: ...
    async def select_plan(self, plans: list) -> int: ...


def extract_json(text: str) -> Optional[dict]:
    """Extract JSON from text response."""
    json_match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass
    return None


class SkillOrchestrator:
    """Orchestrator that uses a single ClaudeSDKClient to maintain context.

    Workflow:
    1. Load skills
    2. Generate execution plan
    3. Execute nodes (all in same session, context preserved)
    """

    def __init__(
        self,
        skill_dir: str = ".claude/skills",
        workspace_dir: str = "workspace",
        max_concurrent: Optional[int] = None,
        node_timeout: Optional[float] = None,
        run_context: Optional[RunContext] = None,
    ):
        cfg = get_config()
        self.skill_dir = Path(skill_dir)
        self.run_context = run_context
        # If run_context exists, use its workspace_dir
        if run_context:
            self.workspace_dir = run_context.workspace_dir
        else:
            self.workspace_dir = Path(workspace_dir)
        self.registry = SkillRegistry(str(skill_dir))
        self.client: Optional[SkillClient] = None
        self.graph: Optional[DependencyGraph] = None
        self.visualizer: Optional[VisualizerProtocol] = None
        self.current_task: str = ""  # Store task for node execution context
        _max_concurrent = max_concurrent if max_concurrent is not None else cfg.max_concurrent
        self.throttler = ExecutionThrottler(max_concurrent=_max_concurrent)
        self.node_timeout = node_timeout if node_timeout is not None else cfg.node_timeout
        # Log queue for async log handling
        self._log_queue: asyncio.Queue = asyncio.Queue()
        self._log_worker_task: Optional[asyncio.Task] = None

    def _start_log_worker(self) -> None:
        """Start the log worker coroutine."""
        if self._log_worker_task is None:
            self._log_worker_task = asyncio.create_task(self._log_worker())

    async def _stop_log_worker(self) -> None:
        """Stop the log worker and flush remaining logs."""
        if self._log_worker_task:
            # Signal worker to stop
            await self._log_queue.put(None)
            await self._log_worker_task
            self._log_worker_task = None

    async def _log_worker(self) -> None:
        """Process logs from queue sequentially."""
        while True:
            item = await self._log_queue.get()
            if item is None:
                # Drain remaining logs
                while not self._log_queue.empty():
                    remaining = self._log_queue.get_nowait()
                    if remaining is not None:
                        await self._process_log(remaining)
                break
            await self._process_log(item)

    async def _process_log(self, item: tuple) -> None:
        """Process a single log item."""
        message, level, node_id = item
        if self.visualizer:
            await self.visualizer.add_log(message, level, node_id=node_id)

    def _enqueue_log(self, message: str, level: str = "info", node_id: Optional[str] = None) -> None:
        """Enqueue a log message for async processing."""
        try:
            self._log_queue.put_nowait((message, level, node_id))
        except Exception:
            pass  # Best effort

    async def run_with_visualizer(
        self,
        task: str,
        skill_names: list[str],
        visualizer: VisualizerProtocol,
        context: Optional[dict] = None,
        plan_only: bool = False,
        files: Optional[list[str]] = None,
    ) -> dict:
        """Run orchestration with an external visualizer.

        Args:
            task: Task description
            skill_names: List of skill names to use
            visualizer: Visualizer instance (Rich or Textual)
            context: Optional context dict
            plan_only: If True, stop after generating plan
            files: Optional list of files to copy into run context
        """
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        result = {"status": "unknown"}

        self.visualizer = visualizer
        self.current_task = task  # Store for node execution context
        viz = visualizer
        # Start log worker for async log processing
        self._start_log_worker()
        await viz.set_task(task)
        await viz.add_log(f"Starting orchestration with skills: {', '.join(skill_names)}", "info")

        # Setup run_context isolated environment
        if self.run_context:
            await viz.add_log(f"Setting up isolated run: {self.run_context.run_id}", "info")
            self.run_context.setup(skill_names, self.skill_dir)
            if files:
                copied = self.run_context.copy_files(files)
                await viz.add_log(f"Files copied to workspace: {copied}", "info")
            self.run_context.save_meta(task, "orchestrated", skill_names)
            if files:
                self.run_context.update_meta(files=files)
            await viz.add_log(f"Skills copied to: {self.run_context.skills_dir}", "info")

        try:
            # Step 1: Load skills
            await viz.add_log("Loading skills...", "info")
            skills = self.registry.find_by_names(skill_names)
            missing = self.registry.get_missing(skill_names)
            if missing:
                await viz.add_log(f"Skills not found: {missing}", "error")
                return {"status": "failed", "error": f"Skills not found: {missing}"}
            await viz.add_log(f"Loaded {len(skills)} skills", "ok")

            # Step 2: Create client with log callback and generate plans
            # Single skill optimization: skip planning API call
            if len(skills) == 0:
                await viz.add_log("No skills selected, Claude will handle directly", "info")
                direct_node = {
                    "id": "ClaudeDirect",
                    "name": "ClaudeDirect",
                    "type": "primary",
                    "depends_on": [],
                    "purpose": "Directly complete the task using available tools",
                    "outputs_summary": "Task output",
                    "downstream_hint": "",
                    "usage_hints": {}
                }
                plans_result = {"plans": [{"name": "Direct Execution", "nodes": [direct_node]}]}
            elif len(skills) == 1:
                await viz.add_log("Single skill detected, skipping orchestration", "info")
                skill = skills[0]
                single_node = {
                    "id": skill.name,
                    "name": skill.name,
                    "type": "primary",
                    "depends_on": [],
                    "purpose": f"Execute {skill.name}",
                    "outputs_summary": "Task output",
                    "downstream_hint": "",
                    "usage_hints": {}
                }
                plans_result = {"plans": [{"name": "Direct Execution", "nodes": [single_node]}]}
            else:
                await viz.add_log("Generating execution plans...", "info")
            # Use log queue for synchronous callback from SkillClient
            def main_log_callback(message: str, level: str = "info") -> None:
                self._enqueue_log(message, level)
            async with SkillClient(
                session_id=f"orch-{task[:20]}",
                log_callback=main_log_callback
            ) as client:
                self.client = client

                if len(skills) > 1:
                    plans_result = await self._generate_plans(task, skills, context)
                if "error" in plans_result:
                    await viz.add_log(f"Planning failed: {plans_result['error']}", "error")
                    return {"status": "failed", "error": plans_result["error"]}

                plans = plans_result.get("plans", [])
                await viz.add_log(f"Generated {len(plans)} plans", "ok")

                if plan_only:
                    result["status"] = "plan_only"
                    result["plans"] = plans
                    await viz.add_log("Stopped after planning (plan_only mode)", "info")
                    return result

                # Let user select a plan (or auto-select if only one)
                if len(plans) > 1:
                    await viz.add_log(f"Waiting for user to select from {len(plans)} plans...", "info")
                    selected_index = await viz.select_plan(plans)
                else:
                    selected_index = 0

                selected_plan = plans[selected_index] if plans else {"nodes": []}
                self.graph = build_graph_from_nodes(selected_plan["nodes"])
                phases = self.graph.get_execution_phases()

                # Save selected execution plan
                if self.run_context:
                    self.run_context.save_plan(selected_plan)

                # Initialize visualizer with nodes
                await viz.set_nodes(selected_plan["nodes"], phases)
                await viz.add_log(f"Selected plan: {selected_plan.get('name', 'Default')}", "info")

                # Step 3: Execute nodes in parallel phases
                for phase_idx, phase in enumerate(phases, 1):
                    await viz.set_phase(phase_idx)
                    await viz.add_log(
                        f"Starting phase {phase_idx}/{len(phases)} "
                        f"({len(phase.nodes)} nodes, mode: {phase.mode})",
                        "info"
                    )

                    # Mark all nodes in phase as running
                    for node_id in phase.nodes:
                        await viz.update_status(node_id, "running")

                    # Execute phase (parallel or sequential based on mode)
                    phase_results = await self._execute_phase_parallel(phase)

                    # Update visualizer with results
                    for node_result in phase_results:
                        status = "completed" if node_result.status == NodeStatus.COMPLETED else "failed"
                        await viz.update_status(node_result.node_id, status)

                        if node_result.status == NodeStatus.COMPLETED:
                            await viz.add_log(
                                f"Node {node_result.node_id} completed "
                                f"({node_result.execution_time_seconds:.1f}s)",
                                "ok"
                            )
                            if node_result.summary:
                                await viz.add_log(f"  Summary: {node_result.summary}", "info")
                        else:
                            await viz.add_log(
                                f"Node {node_result.node_id} failed: {node_result.error or 'unknown'}",
                                "error"
                            )

            # Finalize
            stats = self.graph.get_stats()
            result["status"] = "completed" if stats["failed"] == 0 else "partial"
            result["stats"] = stats
            await viz.add_log(f"Completed: {stats['completed']}/{stats['total']} nodes", "ok")

        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)
            await viz.add_log(f"Error: {e}", "error")
            await viz.add_log(traceback.format_exc(), "error")

        # Save execution result
        if self.run_context:
            self.run_context.save_result(result)
            await viz.add_log(f"Results saved to: {self.run_context.run_dir}", "info")

        # Stop log worker and flush remaining logs
        await self._stop_log_worker()
        self.visualizer = None
        return result

    async def _generate_plans(self, task: str, skills: list[SkillMetadata], context: Optional[dict]) -> dict:
        """Generate multiple execution plans using the client."""
        # Include skill names, descriptions, and content for planning
        skill_info_parts = []
        for s in skills:
            skill_info_parts.append(f"### Skill: {s.name}\n{s.description}\n\n#### Content:\n{s.content}")
        skill_info = "\n\n".join(skill_info_parts)

        context_str = f"\nContext: {json.dumps(context)}" if context else ""

        prompt = build_planner_prompt(task, skill_info, context_str)
        response = await self.client.execute(prompt)
        result = extract_json(response)

        if not result:
            return {"error": "Failed to parse response"}

        # Handle both old format {"nodes": [...]} and new format {"plans": [...]}
        if "plans" in result:
            return result
        elif "nodes" in result:
            return {"plans": [{"name": "Default Plan", "description": "Single execution plan", "nodes": result["nodes"]}]}

        return {"error": "Invalid plan format"}

    async def _execute_node(self, node_id: str) -> dict:
        """Execute a single node using the Skill tool."""
        node = self.graph.get_node(node_id)
        skill = self.registry.get(node.name)

        if not skill:
            self.graph.fail_node(node_id)
            return {"status": "failed", "error": f"Skill {node.name} not found"}

        output_dir = self.workspace_dir / node_id
        output_dir.mkdir(parents=True, exist_ok=True)

        # Determine cwd
        cwd = str(self.run_context.run_dir) if self.run_context else str(self.workspace_dir)

        # Build artifacts context with usage hints (all completed nodes, not just direct dependencies)
        artifact_lines = []
        for nid, n in self.graph.nodes.items():
            if n.status == NodeStatus.COMPLETED and n.output_path:
                # Get usage hint from upstream node for current node
                usage_hint = n.usage_hints.get(node_id, "")
                if usage_hint:
                    artifact_lines.append(
                        f"### {nid} ({n.name})\n"
                        f"- Path: {n.output_path}\n"
                        f"- How to use: {usage_hint}"
                    )
                else:
                    artifact_lines.append(
                        f"### {nid} ({n.name})\n"
                        f"- Path: {n.output_path}"
                    )

        artifacts_context = "\n\n".join(artifact_lines) if artifact_lines else "None (this is the first node)"

        # Build executor prompt with collaboration context and working directory constraint
        prompt = build_executor_prompt(
            skill_name=node.name,
            node_purpose=node.purpose,
            output_dir=str(output_dir),
            artifacts_context=artifacts_context,
            overall_task=self.current_task,
            outputs_summary=node.outputs_summary,
            downstream_hint=node.downstream_hint,
            working_dir=cwd,
        )

        self.graph.update_status(node_id, "running")

        try:
            await self.client.execute(prompt)
            self.graph.update_status(node_id, "completed", str(output_dir))
            return {"status": "completed", "output_path": str(output_dir)}
        except Exception as e:
            self.graph.fail_node(node_id)
            error_msg = str(e)
            if self.visualizer:
                asyncio.create_task(self.visualizer.add_log(f"Node {node_id} error: {error_msg}", "error"))
                asyncio.create_task(self.visualizer.add_log(traceback.format_exc(), "error"))
            return {"status": "failed", "error": error_msg}

    async def _execute_phase_parallel(
        self, phase: ExecutionPhase
    ) -> list[NodeExecutionResult]:
        """Execute all nodes in a phase concurrently using isolated sessions.

        Args:
            phase: The execution phase containing nodes to execute

        Returns:
            List of NodeExecutionResult for each node
        """
        # Build tasks for throttler
        tasks = [
            (lambda nid=node_id: self._execute_node_isolated(nid), node_id)
            for node_id in phase.nodes
        ]

        # Execute with throttling
        results = await self.throttler.execute_batch(tasks)

        # Update graph state and cascade failures
        for result in results:
            if result.status == NodeStatus.COMPLETED:
                self.graph.update_status(
                    result.node_id, "completed", result.output_path
                )
            else:
                self.graph.fail_node(result.node_id)

        return results

    async def _execute_node_isolated(self, node_id: str) -> NodeExecutionResult:
        """Execute a single node in a completely isolated session.

        This creates an independent session for the node, preventing context
        pollution between nodes and enabling true parallel execution.

        Args:
            node_id: The ID of the node to execute

        Returns:
            NodeExecutionResult with execution details
        """
        node = self.graph.get_node(node_id)

        # Special handling: Claude Direct node (no skill)
        if node.name == "ClaudeDirect":
            return await self._execute_claude_direct(node_id, node)

        skill = self.registry.get(node.name)
        start_time = time.time()

        if not skill:
            return NodeExecutionResult(
                node_id=node_id,
                status=NodeStatus.FAILED,
                error=f"Skill {node.name} not found",
                failure_reason=NodeFailureReason.SKILL_ERROR,
                execution_time_seconds=time.time() - start_time,
            )

        output_dir = self.workspace_dir / node_id
        output_dir.mkdir(parents=True, exist_ok=True)

        # Determine cwd: if run_context exists, use run_dir (where skills are located)
        if self.run_context:
            cwd = str(self.run_context.run_dir)
        else:
            cwd = str(self.workspace_dir)

        # Build artifacts context
        artifacts_context = self._build_artifacts_context(node_id)

        # Build isolated executor prompt with working directory constraint
        prompt = build_isolated_executor_prompt(
            overall_task=self.current_task,
            skill_name=node.name,
            node_purpose=node.purpose,
            artifacts_context=artifacts_context,
            output_dir=str(output_dir),
            outputs_summary=node.outputs_summary,
            downstream_hint=node.downstream_hint,
            working_dir=cwd,
        )

        # Create log callback that includes node_id
        # Use log queue for synchronous callback from SkillClient
        def node_log_callback(message: str, level: str = "info") -> None:
            self._enqueue_log(message, level, node_id=node_id)

        try:

            # Create an isolated session for this node
            async with SkillClient(
                session_id=f"node-{node_id}",
                cwd=cwd,  # Claude SDK discovers skills from {cwd}/.claude/skills/
                log_callback=node_log_callback,
            ) as client:
                response = await asyncio.wait_for(
                    client.execute(prompt),
                    timeout=self.node_timeout,
                )

                summary, is_success = self._extract_execution_summary(response)

                if is_success:
                    return NodeExecutionResult(
                        node_id=node_id,
                        status=NodeStatus.COMPLETED,
                        output_path=str(output_dir),
                        summary=summary,
                        failure_reason=NodeFailureReason.SUCCESS,
                        execution_time_seconds=time.time() - start_time,
                    )
                else:
                    return NodeExecutionResult(
                        node_id=node_id,
                        status=NodeStatus.FAILED,
                        output_path=str(output_dir),
                        summary=summary,
                        error="Agent reported task failure in execution summary",
                        failure_reason=NodeFailureReason.SKILL_ERROR,
                        execution_time_seconds=time.time() - start_time,
                    )

        except asyncio.TimeoutError:
            return NodeExecutionResult(
                node_id=node_id,
                status=NodeStatus.FAILED,
                error=f"Execution timed out after {self.node_timeout}s",
                failure_reason=NodeFailureReason.TIMEOUT,
                execution_time_seconds=self.node_timeout,
            )
        except Exception as e:
            return NodeExecutionResult(
                node_id=node_id,
                status=NodeStatus.FAILED,
                error=str(e),
                failure_reason=NodeFailureReason.UNKNOWN,
                execution_time_seconds=time.time() - start_time,
            )

    def _build_artifacts_context(self, node_id: str) -> str:
        """Build artifacts context string for a node.

        Collects output paths and usage hints from all completed upstream nodes.

        Args:
            node_id: The target node ID

        Returns:
            Formatted string describing available artifacts
        """
        artifact_lines = []

        for nid, n in self.graph.nodes.items():
            if n.status == NodeStatus.COMPLETED and n.output_path:
                usage_hint = n.usage_hints.get(node_id, "")
                if usage_hint:
                    artifact_lines.append(
                        f"### {nid} ({n.name})\n"
                        f"- Path: {n.output_path}\n"
                        f"- How to use: {usage_hint}"
                    )
                else:
                    artifact_lines.append(
                        f"### {nid} ({n.name})\n"
                        f"- Path: {n.output_path}"
                    )

        if artifact_lines:
            return "\n\n".join(artifact_lines)
        return "None (this is the first node)"

    def _extract_execution_summary(self, response: str) -> tuple[str, bool]:
        """Extract execution summary and status from response.

        Looks for content within <execution_summary> tags and parses STATUS field.

        Args:
            response: The full response text

        Returns:
            Tuple of (summary_text, success_bool)
            - summary_text: The extracted summary content
            - success_bool: True if STATUS is SUCCESS or not specified, False if FAILURE
        """
        match = re.search(
            r"<execution_summary>(.*?)</execution_summary>",
            response,
            re.DOTALL,
        )
        if match:
            summary = match.group(1).strip()
            # Check for STATUS field
            status_match = re.search(r"STATUS:\s*(SUCCESS|FAILURE)", summary, re.IGNORECASE)
            if status_match:
                is_success = status_match.group(1).upper() == "SUCCESS"
                return summary, is_success
            # No STATUS field found, assume success (backward compatibility)
            return summary, True
        # No summary found, assume success
        return "", True

    async def _execute_claude_direct(self, node_id: str, node) -> NodeExecutionResult:
        """Execute task directly with Claude without any skill."""
        start_time = time.time()

        output_dir = self.workspace_dir / node_id
        output_dir.mkdir(parents=True, exist_ok=True)

        # Determine cwd
        cwd = str(self.run_context.run_dir) if self.run_context else str(self.workspace_dir)

        # Build direct execution prompt with working directory constraint
        prompt = build_direct_executor_prompt(
            task=self.current_task,
            output_dir=str(output_dir),
            working_dir=cwd,
        )

        def node_log_callback(message: str, level: str = "info") -> None:
            self._enqueue_log(message, level, node_id=node_id)

        try:

            # Disable Skill tool since no skills are available
            async with SkillClient(
                session_id=f"node-{node_id}",
                cwd=cwd,
                log_callback=node_log_callback,
                disallowed_tools=["Skill"],
            ) as client:
                response = await asyncio.wait_for(
                    client.execute(prompt),
                    timeout=self.node_timeout,
                )

                summary, is_success = self._extract_execution_summary(response)

                return NodeExecutionResult(
                    node_id=node_id,
                    status=NodeStatus.COMPLETED if is_success else NodeStatus.FAILED,
                    output_path=str(output_dir),
                    summary=summary or "Claude direct execution completed",
                    failure_reason=NodeFailureReason.SUCCESS if is_success else NodeFailureReason.EXECUTION_ERROR,
                    execution_time_seconds=time.time() - start_time,
                )
        except asyncio.TimeoutError:
            return NodeExecutionResult(
                node_id=node_id,
                status=NodeStatus.FAILED,
                error=f"Execution timed out after {self.node_timeout}s",
                failure_reason=NodeFailureReason.TIMEOUT,
                execution_time_seconds=self.node_timeout,
            )
        except Exception as e:
            return NodeExecutionResult(
                node_id=node_id,
                status=NodeStatus.FAILED,
                error=str(e),
                failure_reason=NodeFailureReason.EXECUTION_ERROR,
                execution_time_seconds=time.time() - start_time,
            )


def run_orchestrator(
    task: str,
    skill_names: list[str],
    skill_dir: str = ".claude/skills",
    workspace_dir: str = "workspace",
    context: Optional[dict] = None,
    plan_only: bool = False,
    port: int = 8765,
    files: Optional[list[str]] = None,
    mode: str = None,
    task_name: str = None,
) -> dict:
    """Run the orchestrator with Web UI.

    Args:
        task: Task description
        skill_names: List of skill names to use
        skill_dir: Directory containing skills
        workspace_dir: Directory for outputs
        context: Optional context dict
        plan_only: Stop after planning
        port: Web UI port (default 8765)
        files: Optional list of files to copy into run context
        mode: Execution mode for folder naming (dag, auto_selected, auto_all, baseline)
        task_name: Optional user-provided task name for folder naming
    """
    from skill_orchestrator.web_ui import run_with_web_ui, WebVisualizer

    # Create isolated execution context
    run_context = RunContext.create(task, mode=mode, task_name=task_name)

    orchestrator = SkillOrchestrator(
        skill_dir=skill_dir,
        workspace_dir=workspace_dir,
        run_context=run_context,
    )

    async def run_orchestration(visualizer: WebVisualizer) -> dict:
        return await orchestrator.run_with_visualizer(
            task=task,
            skill_names=skill_names,
            visualizer=visualizer,
            context=context,
            plan_only=plan_only,
            files=files,
        )

    return run_with_web_ui(run_orchestration, port=port)
