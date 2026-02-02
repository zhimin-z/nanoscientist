"""Load and parse SKILL.md files from scientific-skills directory"""
from pathlib import Path
import yaml


def load_skill(skill_path):
    """
    Load a SKILL.md file and return its content

    Args:
        skill_path: Path to SKILL.md file (e.g., "scientific-skills/literature-survey/SKILL.md")

    Returns:
        dict: Parsed skill with frontmatter and body
            {
                "name": "literature-survey",
                "description": "...",
                "body": "Full markdown content..."
            }
    """
    path = Path(skill_path)
    if not path.exists():
        raise FileNotFoundError(f"Skill file not found: {skill_path}")

    content = path.read_text()

    # Parse frontmatter (YAML between ---) and body
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            frontmatter = yaml.safe_load(parts[1])
            body = parts[2].strip()
        else:
            frontmatter = {}
            body = content
    else:
        frontmatter = {}
        body = content

    return {
        "name": frontmatter.get("name", path.parent.name),
        "description": frontmatter.get("description", ""),
        "frontmatter": frontmatter,
        "body": body,
        "full_content": content
    }


def get_skill_instructions(skill_path):
    """
    Get just the instruction body (no frontmatter) from a skill

    Args:
        skill_path: Path to SKILL.md file

    Returns:
        str: The skill instructions (markdown body)
    """
    skill = load_skill(skill_path)
    return skill["body"]
