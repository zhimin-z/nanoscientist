#!/usr/bin/env python3
"""Autonomous Scientist — budget-aware research agent.

Usage:
    python main.py "Your research topic" --budget 1.00
    python main.py "Quick question" --budget 0.05
    python main.py --list-skills
"""

import argparse
import sys
from pathlib import Path

# Resolve project paths
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = PROJECT_ROOT / "skills"

from utils import load_skill_index
from flow import create_scientist_flow


def list_skills():
    """Print available skills (from skills.json index)."""
    index = load_skill_index(str(SKILLS_DIR))
    print(f"\nAvailable Skills ({len(index)}):")
    print("-" * 50)
    for name, desc in sorted(index.items()):
        short = desc[:80] + "..." if len(desc) > 80 else desc
        print(f"  {name}: {short}")
    print()


def run(topic: str, budget: float, output_dir: str):
    """Run the autonomous scientist flow."""
    # Load lightweight skill index (descriptions only, not full SKILL.md)
    skill_index = load_skill_index(str(SKILLS_DIR))
    print(f"Loaded {len(skill_index)} skills from skills.json")

    # Initialize shared store
    shared = {
        "topic": topic,
        "budget_dollars": budget,
        "budget_remaining": budget,
        "cost_log": [],
        "skill_index": skill_index,       # lightweight: {name: description}
        "skills_dir": str(SKILLS_DIR),    # path for lazy-loading SKILL.md
        "output_dir": output_dir,
    }

    # Create and run flow
    flow = create_scientist_flow()

    print(f"\n{'='*50}")
    print(f"Autonomous Scientist")
    print(f"Topic: {topic}")
    print(f"Budget: ${budget:.2f}")
    print(f"{'='*50}\n")

    flow.run(shared)

    return shared


def main():
    parser = argparse.ArgumentParser(
        description="Autonomous Scientist — budget-aware research agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py "What are CRISPR off-target effects?" --budget 1.00
  python main.py "Latest in quantum error correction" --budget 0.05
  python main.py --list-skills
        """,
    )
    parser.add_argument("topic", nargs="?", help="Research topic or question")
    parser.add_argument(
        "--budget", "-b", type=float, default=0.50,
        help="Budget in USD for LLM inference (default: $0.50)",
    )
    parser.add_argument(
        "--output", "-o", default="research_outputs",
        help="Output directory (default: research_outputs/)",
    )
    parser.add_argument(
        "--list-skills", action="store_true", help="List available skills",
    )

    args = parser.parse_args()

    if args.list_skills:
        list_skills()
        return

    if not args.topic:
        parser.print_help()
        sys.exit(1)

    try:
        shared = run(args.topic, args.budget, args.output)
        # Exit 0 if we got output
        if shared.get("output_path"):
            sys.exit(0)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)


if __name__ == "__main__":
    main()
