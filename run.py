#!/usr/bin/env python
"""
AgentSkillOS - Intelligent Task Orchestration.

Usage:
    python run.py                    # Launch Web UI (default)
    python run.py --port 8080        # Specify port
    python run.py --no-browser       # Don't auto-open browser
    python run.py build              # Build capability tree
    python run.py build -g top500    # Build for specific skill group

Configuration:
    - config.yaml: User configuration (skill_group, max_skills, port, etc.)
    - .env: Sensitive information (API keys)
"""
import sys
from pathlib import Path

# Add src to path for direct execution
sys.path.insert(0, str(Path(__file__).parent / "src"))

from cli import main

if __name__ == "__main__":
    main()
