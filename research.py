#!/usr/bin/env python3
"""
Minimalist Scientist - Research pipeline via AgentSkillOS.

Usage:
    python research.py "Your research question here"
    python research.py --skills literature-survey,paper-writing "Quick survey topic"
    python research.py --list-skills
"""
import asyncio
import argparse
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from rich.console import Console
from rich.table import Table

import config as _  # noqa: F401  (triggers .env loading + config validation)
from config import get_config, SKILLS_DIR
from skill_orchestrator.orchestrator import SkillOrchestrator
from skill_orchestrator.null_visualizer import NullVisualizer
from skill_orchestrator.run_context import RunContext
from skill_orchestrator.skills import SkillRegistry

console = Console()

# Default scientific pipeline skills (full research pipeline)
DEFAULT_SKILLS = [
    "literature-survey",
    "method-implementation",
    "experimental-evaluation",
    "paper-writing",
]


class RichVisualizer(NullVisualizer):
    """CLI visualizer with rich console output."""

    async def set_task(self, task: str) -> None:
        console.print(f"\n[bold blue]Research Task:[/bold blue] {task}\n")

    async def set_nodes(self, nodes: list, phases: list) -> None:
        table = Table(title="Execution Plan")
        table.add_column("Phase", style="cyan")
        table.add_column("Skill", style="green")
        table.add_column("Purpose", style="white")
        for phase in phases:
            for node_id in phase.nodes:
                node = next((n for n in nodes if n["id"] == node_id), {})
                table.add_row(
                    str(phase.phase_number),
                    node.get("name", node_id),
                    node.get("purpose", ""),
                )
        console.print(table)
        console.print()

    async def update_status(self, node_id: str, status: str) -> None:
        icons = {"running": "[yellow]>>>[/yellow]", "completed": "[green]OK[/green]", "failed": "[red]FAIL[/red]"}
        icon = icons.get(status, status)
        console.print(f"  {icon} {node_id}: {status}")

    async def set_phase(self, phase_num: int) -> None:
        console.print(f"\n[bold cyan]--- Phase {phase_num} ---[/bold cyan]")

    async def add_log(self, message: str, level: str = "info", node_id: str = None) -> None:
        await super().add_log(message, level, node_id)
        colors = {"ok": "green", "error": "red", "warn": "yellow", "info": "dim", "send": "blue", "recv": "white", "tool": "magenta"}
        color = colors.get(level, "white")
        prefix = f"[{node_id}] " if node_id else ""
        if level in ("ok", "error", "warn"):
            console.print(f"  [{color}]{prefix}{message}[/{color}]")

    async def select_plan(self, plans: list) -> int:
        if len(plans) == 1:
            return 0
        console.print("\n[bold]Available execution plans:[/bold]")
        for i, plan in enumerate(plans):
            console.print(f"  [{i}] {plan.get('name', f'Plan {i}')}: {plan.get('description', '')}")
        while True:
            try:
                choice = int(input(f"\nSelect plan (0-{len(plans)-1}): "))
                if 0 <= choice < len(plans):
                    return choice
            except (ValueError, EOFError):
                pass
            console.print("[red]Invalid choice, try again[/red]")


def list_skills():
    """List all available skills."""
    registry = SkillRegistry(str(SKILLS_DIR))
    skills = registry.list_all()

    table = Table(title=f"Available Skills ({len(skills)})")
    table.add_column("Name", style="green")
    table.add_column("Category", style="cyan")
    table.add_column("Description", style="white", max_width=60)

    for skill in sorted(skills, key=lambda s: s.name):
        desc = skill.description[:60] + "..." if len(skill.description) > 60 else skill.description
        table.add_row(skill.name, skill.category, desc)

    console.print(table)


async def run_research(task: str, skill_names: list[str], verbose: bool = False):
    """Run the research pipeline."""
    cfg = get_config()

    # Create run context
    run_context = RunContext.create(task, base_dir="research_outputs", mode="dag", task_name="research")

    # Create orchestrator
    orchestrator = SkillOrchestrator(
        skill_dir=str(SKILLS_DIR),
        workspace_dir="research_outputs",
        run_context=run_context,
    )

    # Use rich visualizer for CLI output
    visualizer = RichVisualizer()

    result = await orchestrator.run_with_visualizer(
        task=task,
        skill_names=skill_names,
        visualizer=visualizer,
    )

    # Print summary
    console.print(f"\n[bold]{'=' * 60}[/bold]")
    status_color = "green" if result.get("status") == "completed" else "yellow" if result.get("status") == "partial" else "red"
    console.print(f"[bold {status_color}]Result: {result.get('status', 'unknown')}[/bold {status_color}]")

    if "stats" in result:
        stats = result["stats"]
        console.print(f"Completed: {stats.get('completed', 0)}/{stats.get('total', 0)} skills")

    if run_context:
        console.print(f"Output: {run_context.run_dir}")

    console.print(f"[bold]{'=' * 60}[/bold]\n")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Minimalist Scientist - Research pipeline via AgentSkillOS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python research.py "Compare quicksort vs mergesort performance"
  python research.py --skills literature-survey,paper-writing "Quick survey on LLM agents"
  python research.py --list-skills
        """,
    )
    parser.add_argument("task", nargs="?", help="Research question or task description")
    parser.add_argument("--skills", "-s", help="Comma-separated list of skills (default: full pipeline)")
    parser.add_argument("--list-skills", action="store_true", help="List all available skills")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--config", "-c", metavar="PATH", help="Path to config.yaml")

    args = parser.parse_args()

    # Initialize config
    if args.config:
        from config import Config
        Config.reset()
        get_config(config_path=args.config)

    if args.list_skills:
        list_skills()
        return

    if not args.task:
        parser.print_help()
        sys.exit(1)

    # Determine skills to use
    if args.skills:
        skill_names = [s.strip() for s in args.skills.split(",")]
    else:
        skill_names = DEFAULT_SKILLS

    console.print(f"[bold]Minimalist Scientist[/bold]")
    console.print(f"Skills: {', '.join(skill_names)}\n")

    try:
        result = asyncio.run(run_research(args.task, skill_names, verbose=args.verbose))
        sys.exit(0 if result.get("status") in ("completed", "partial") else 1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
