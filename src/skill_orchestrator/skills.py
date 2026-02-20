"""Skill loader - Parse and discover skills from SKILL.md files."""

import re
from pathlib import Path
from typing import Optional

from skill_orchestrator.models import SkillMetadata


def parse_frontmatter(content: str) -> dict[str, str]:
    """Parse YAML frontmatter from SKILL.md content."""
    frontmatter = {}
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return frontmatter

    yaml_content = match.group(1)
    for line in yaml_content.split("\n"):
        line = line.strip()
        if ":" in line:
            key, _, value = line.partition(":")
            frontmatter[key.strip()] = value.strip().strip('"').strip("'")

    return frontmatter


def load_skill(skill_path: Path, max_content_chars: int = 5000) -> Optional[SkillMetadata]:
    """Load metadata from a single skill directory."""
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return None

    try:
        content = skill_md.read_text(encoding="utf-8")
        fm = parse_frontmatter(content)

        allowed_tools = []
        if "allowed-tools" in fm:
            tools_str = fm["allowed-tools"].strip("[]")
            allowed_tools = [t.strip() for t in tools_str.split(",") if t.strip()]

        return SkillMetadata(
            name=fm.get("name", skill_path.name),
            description=fm.get("description", ""),
            path=str(skill_path),
            allowed_tools=allowed_tools,
            category=fm.get("category", "other"),
            content=content[:max_content_chars],
        )
    except Exception:
        return None


def load_skill_content(skill_path: Path, max_chars: int = 4000) -> str:
    """Load full SKILL.md content (truncated for context management)."""
    skill_md = skill_path / "SKILL.md"
    if skill_md.exists():
        content = skill_md.read_text(encoding="utf-8")
        return content[:max_chars]
    return f"Skill not found at {skill_path}"


class SkillRegistry:
    """Registry of available skills."""

    def __init__(self, skill_dir: str = ".claude/skills"):
        self.skill_dir = Path(skill_dir)
        self._cache: dict[str, SkillMetadata] = {}

    def list_all(self) -> list[SkillMetadata]:
        """List all available skills."""
        if not self._cache:
            self._build_cache()
        return list(self._cache.values())

    def get(self, name: str) -> Optional[SkillMetadata]:
        """Get a skill by name."""
        if not self._cache:
            self._build_cache()
        return self._cache.get(name)

    def find_by_names(self, names: list[str]) -> list[SkillMetadata]:
        """Find skills by name list."""
        return [s for name in names if (s := self.get(name))]

    def get_missing(self, names: list[str]) -> list[str]:
        """Get list of skill names that were not found."""
        found = {s.name for s in self.find_by_names(names)}
        return [n for n in names if n not in found]

    def refresh(self) -> None:
        """Clear cache and rebuild."""
        self._cache.clear()
        self._build_cache()

    def _build_cache(self) -> None:
        """Build the skill cache by scanning the skill directory."""
        if not self.skill_dir.is_dir():
            return
        for item in self.skill_dir.iterdir():
            if item.is_dir():
                if skill := load_skill(item):
                    self._cache[skill.name] = skill

    def __len__(self) -> int:
        if not self._cache:
            self._build_cache()
        return len(self._cache)

    def __contains__(self, name: str) -> bool:
        return self.get(name) is not None
