#!/usr/bin/env python
"""
AgentSkillOS CLI.

Usage:
    python run.py                          # Start Web UI
    python run.py --port 8080              # Specify port
    python run.py --no-browser             # Don't auto-open browser
    python run.py build                    # Build capability tree
    python run.py build -g top500          # Build tree for specific skill group
"""

import argparse
import sys

from rich.console import Console

# Load unified config (triggers .env loading and validates config.yaml)
import config  # noqa: F401
from config import get_config, Config, SKILL_GROUPS, SKILL_GROUP_ALIASES

console = Console()


# =============================================================================
# Web UI Command
# =============================================================================

def cmd_ui(args):
    """Launch unified Web UI."""
    from unified_service import run_unified_service

    cfg = get_config({"port": args.port})

    console.print(f"Starting Web UI at http://127.0.0.1:{cfg.port}")
    console.print("Press Ctrl+C to stop")

    run_unified_service(
        host="127.0.0.1",
        port=cfg.port,
        open_browser=not args.no_browser,
    )


# =============================================================================
# Build Command
# =============================================================================

def cmd_build(args):
    """Build capability tree from skill_seeds."""
    from skill_retriever.tree.builder import TreeBuilder

    skills_dir = None
    output_path = None

    if args.skill_group:
        # Resolve alias for backward compatibility (e.g., "default" -> "skill_seeds")
        resolved_group = SKILL_GROUP_ALIASES.get(args.skill_group, args.skill_group)
        group = next((g for g in SKILL_GROUPS if g["id"] == resolved_group), None)
        if not group:
            console.print(f"[red]Unknown skill group: {args.skill_group}[/red]")
            sys.exit(1)
        skills_dir = group["skills_dir"]
        output_path = group["tree_path"]
        console.print(f"[dim]Building tree for skill group: {resolved_group}[/dim]")

    builder = TreeBuilder(skills_dir=skills_dir, output_path=output_path)
    builder.build(
        verbose=args.verbose,
        show_tree=not args.quiet,
        generate_html=not args.no_html,
    )


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="AgentSkillOS - Intelligent Task Orchestration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py                                # Start Web UI
  python run.py --port 8080                    # Specify port
  python run.py --no-browser                   # Don't auto-open browser
  python run.py --config path/to/config.yaml   # Use custom config

  python run.py build                          # Build capability tree
  python run.py build -g top500                # Build tree for specific skill group
        """,
    )

    # Global config option (must be before subparsers)
    parser.add_argument("--config", "-c", metavar="PATH",
                        help="Path to config.yaml file (default: config/config.yaml)")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # -------------------------------------------------------------------------
    # Default: Web UI (when no subcommand)
    # -------------------------------------------------------------------------
    parser.add_argument("--port", type=int, default=None,
                        help="Web UI port (default: from config.yaml)")
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't open browser automatically")

    # -------------------------------------------------------------------------
    # Subcommand: build
    # -------------------------------------------------------------------------
    build_parser = subparsers.add_parser("build", help="Build capability tree")
    build_parser.add_argument("--skill-group", "-g",
                              help="Skill group to build (e.g., 'skill_seeds', 'top500')")
    build_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    build_parser.add_argument("--quiet", "-q", action="store_true", help="Don't show tree")
    build_parser.add_argument("--no-html", action="store_true", help="Skip HTML generation")
    build_parser.set_defaults(func=cmd_build)

    # -------------------------------------------------------------------------
    # Parse arguments
    # -------------------------------------------------------------------------
    args = parser.parse_args()

    # Initialize config (with custom path if specified)
    if args.config:
        Config.reset()
        get_config(config_path=args.config)
    else:
        # Validate default config exists
        get_config()

    try:
        # Handle subcommands
        if args.command and hasattr(args, "func"):
            args.func(args)
            return

        # Default: launch Web UI
        cmd_ui(args)

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
