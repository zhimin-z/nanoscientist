"""Utility functions for the Autonomous Scientist agent."""

import json
import os
import re
import yaml
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# --- LLM Configuration ---
MODEL = "minimax/minimax-m2.5"
INPUT_COST_PER_M = 0.30   # $/M input tokens
OUTPUT_COST_PER_M = 1.10  # $/M output tokens


def get_client() -> OpenAI:
    """Create OpenRouter-compatible OpenAI client.

    Looks for OPENROUTER_API_KEY in environment / .env.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENROUTER_API_KEY not found.\n"
            "Add to your .env file:\n"
            "  OPENROUTER_API_KEY=sk-or-v1-your-key-here\n"
            "Get one at https://openrouter.ai/keys"
        )
    return OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )


def call_llm(prompt: str, system: str = None) -> tuple[str, dict]:
    """Call the LLM and return (response_text, usage_dict).

    usage_dict has keys: input_tokens, output_tokens, cost
    """
    client = get_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
    )

    text = response.choices[0].message.content or ""
    usage = response.usage
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0
    cost = (
        input_tokens * INPUT_COST_PER_M / 1_000_000
        + output_tokens * OUTPUT_COST_PER_M / 1_000_000
    )

    return text, {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost": cost,
    }


def load_skill_index(skills_dir: str) -> dict[str, str]:
    """Load skill index from skills.json — lightweight {name: description}.

    skills.json acts as a DB index: only descriptions are loaded,
    full SKILL.md content is loaded on demand via load_skill_content().
    """
    index_path = Path(skills_dir) / "skills.json"
    if not index_path.exists():
        raise ValueError(f"skills.json not found at {index_path}")
    data = json.loads(index_path.read_text(encoding="utf-8"))
    index = {}
    for skill in data.get("skills", []):
        index[skill["id"]] = skill.get("description", skill["id"])
    if not index:
        raise ValueError(f"No skills found in {index_path}")
    return index


def load_skill_content(skills_dir: str, skill_name: str) -> str:
    """Lazy-load a single SKILL.md on demand (called by ExecuteSkill only)."""
    skill_file = Path(skills_dir) / skill_name / "SKILL.md"
    if not skill_file.exists():
        raise FileNotFoundError(f"SKILL.md not found: {skill_file}")
    return skill_file.read_text(encoding="utf-8")


def format_skill_index(index: dict[str, str]) -> str:
    """Format the skill index as a readable list for LLM prompts."""
    lines = []
    for name, desc in sorted(index.items()):
        short = desc[:117] + "..." if len(desc) > 120 else desc
        lines.append(f"- {name}: {short}")
    return "\n".join(lines)


def parse_yaml_response(text: str) -> dict:
    """Extract and parse a YAML block from an LLM response."""
    match = re.search(r"```yaml(.*?)```", text, re.DOTALL | re.IGNORECASE)
    block = match.group(1).strip() if match else text.strip()
    return yaml.safe_load(block)


def extract_bibtex(text: str) -> tuple[str, list[str]]:
    """Split LLM response into (main_content, list_of_bibtex_entries).

    The LLM is instructed to put BibTeX in a ```bibtex ... ``` block.
    """
    bib_match = re.search(r"```bibtex(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if not bib_match:
        return text.strip(), []

    main_content = text[: bib_match.start()].strip()
    bib_block = bib_match.group(1).strip()

    # Split into individual entries
    entries = re.findall(r"(@\w+\{[^@]+)", bib_block, re.DOTALL)
    entries = [e.strip() for e in entries if e.strip()]
    return main_content, entries


def dedup_bibtex(entries: list[str]) -> str:
    """Deduplicate BibTeX entries by cite key, return combined .bib content."""
    seen = {}
    for entry in entries:
        match = re.match(r"@\w+\{([^,]+),", entry)
        if match:
            key = match.group(1).strip()
            if key not in seen:
                seen[key] = entry
    return "\n\n".join(seen.values()) + "\n" if seen else ""


def load_quality_standard(docs_dir: str = "docs") -> str:
    """Load the paper quality standard from docs/PAPER_QUALITY_STANDARD.md.

    Returns the full text, which nodes can excerpt as needed for prompts.
    """
    std_path = Path(docs_dir) / "PAPER_QUALITY_STANDARD.md"
    if not std_path.exists():
        return ""
    return std_path.read_text(encoding="utf-8")


def track_cost(shared: dict, step: str, usage: dict):
    """Append cost to shared ledger and decrement remaining budget."""
    shared.setdefault("cost_log", []).append({"step": step, **usage})
    shared["budget_remaining"] = shared.get("budget_remaining", 0) - usage["cost"]
