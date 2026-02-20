"""
Allow running as: python -m skill_retriever

Redirects to the unified CLI with appropriate subcommands.
Usage:
    python -m skill_retriever build
    python -m skill_retriever search "query"
    python -m skill_retriever list
"""
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from cli import main

if __name__ == "__main__":
    main()
