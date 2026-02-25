"""Utility functions for the Autonomous Scientist agent."""

import json
import os
import re
import yaml
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv


def init_env(env_path: str = None):
    """Load environment variables from the specified .env file.

    If no path given, defaults to .env in the project root.
    """
    if env_path is None:
        env_path = str(Path(__file__).resolve().parents[1] / ".env")
    path = Path(env_path)
    if not path.exists():
        raise FileNotFoundError(f".env file not found: {path}")
    load_dotenv(path, override=True)


# --- API Key Registry ---
# Maps environment variable names to what they unlock for the scientist.
API_KEY_REGISTRY = {
    "OPENROUTER_API_KEY": "Core LLM inference, research-lookup, generate-image, scientific-schematics, scientific-slides, paper-2-web",
    "ANTHROPIC_API_KEY": "Anthropic Claude models",
    "PERPLEXITY_API_KEY": "Perplexity Sonar search (used by research-lookup)",
    "OPENAI_API_KEY": "OpenAI models, paper-2-web",
    "HF_TOKEN": "Hugging Face models and datasets (used by tooluniverse)",
    "GITHUB_TOKEN": "GitHub API access",
    "GITLAB_TOKEN": "GitLab API access",
}


def detect_api_keys() -> dict[str, bool]:
    """Check which API keys are available in the environment.

    Returns a dict of {key_name: is_set} for all known keys.
    Must be called AFTER init_env().
    """
    return {key: bool(os.environ.get(key)) for key in API_KEY_REGISTRY}


def format_available_keys(keys: dict[str, bool]) -> str:
    """Format detected API keys as a readable summary for LLM prompts."""
    lines = []
    for key, is_set in keys.items():
        status = "available" if is_set else "NOT SET"
        desc = API_KEY_REGISTRY.get(key, "")
        lines.append(f"- {key}: {status} — {desc}")
    return "\n".join(lines)

# --- LLM Configuration ---
MODEL = "z-ai/glm-5"
INPUT_COST_PER_M = 0.95   # $/M input tokens
OUTPUT_COST_PER_M = 2.55  # $/M output tokens


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


def load_skill_content(skills_dir: str, skill_name: str) -> tuple[str, dict]:
    """Lazy-load a single SKILL.md on demand (called by ExecuteSkill only).

    Returns (content, metadata) where metadata is the parsed YAML frontmatter.
    Content has the frontmatter stripped.
    """
    skill_file = Path(skills_dir) / skill_name / "SKILL.md"
    if not skill_file.exists():
        raise FileNotFoundError(f"SKILL.md not found: {skill_file}")
    raw = skill_file.read_text(encoding="utf-8")

    # Parse YAML frontmatter (between --- delimiters)
    metadata = {}
    content = raw
    fm_match = re.match(r'^---\s*\n(.*?)\n---\s*\n', raw, re.DOTALL)
    if fm_match:
        try:
            metadata = yaml.safe_load(fm_match.group(1)) or {}
        except yaml.YAMLError:
            metadata = {}
        content = raw[fm_match.end():]

    # Normalize allowed-tools to a list of strings
    tools = metadata.get("allowed-tools", [])
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",")]
    metadata["allowed-tools"] = tools

    return content.strip(), metadata


def format_skill_index(index: dict[str, str]) -> str:
    """Format the skill index as a readable list for LLM prompts."""
    lines = []
    for name, desc in sorted(index.items()):
        short = desc[:117] + "..." if len(desc) > 120 else desc
        lines.append(f"- {name}: {short}")
    return "\n".join(lines)


def parse_yaml_response(text: str) -> dict:
    """Extract and parse a YAML block from an LLM response."""
    # Try ```yaml, ```yml, ```YAML
    match = re.search(r"```(?:yaml|yml)\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if not match:
        # Try any fenced block that looks like YAML (has "key:" patterns)
        for m in re.finditer(r"```\w*\s*(.*?)```", text, re.DOTALL):
            if re.search(r"^\s*\w+\s*:", m.group(1), re.MULTILINE):
                match = m
                break
    block = match.group(1).strip() if match else text.strip()
    try:
        result = yaml.safe_load(block)
        return result if isinstance(result, dict) else None
    except yaml.YAMLError:
        return None


def extract_bibtex(text: str) -> tuple[str, list[str]]:
    """Split LLM response into (main_content, list_of_bibtex_entries).

    Handles multiple LLM output formats:
    - ```bibtex ... ```  (standard)
    - ```bib ... ```     (common variant)
    - ``` ... ```        (no language tag, if it contains @article etc.)
    - Raw @article{...}  entries outside code blocks (fallback)
    """
    # Try fenced code blocks: ```bibtex, ```bib, ```BibTeX, ```latex (with bibtex inside)
    bib_match = re.search(r"```(?:bibtex|bib|BibTeX)\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)

    if not bib_match:
        # Try generic fenced block that contains @article/@inproceedings etc.
        for m in re.finditer(r"```\w*\s*(.*?)```", text, re.DOTALL):
            if re.search(r"@\w+\{", m.group(1)):
                bib_match = m
                break

    if bib_match:
        main_content = text[: bib_match.start()].strip()
        bib_block = bib_match.group(1).strip()
    else:
        # Fallback: extract raw @type{...} entries from the end of the text
        raw_entries = list(re.finditer(r"@\w+\{", text))
        if raw_entries:
            first_entry_pos = raw_entries[0].start()
            main_content = text[:first_entry_pos].strip()
            bib_block = text[first_entry_pos:].strip()
        else:
            return text.strip(), []

    # Split into individual entries
    entries = re.findall(r"(@\w+\{[^@]+)", bib_block, re.DOTALL)
    entries = [e.strip() for e in entries if e.strip()]
    return main_content, entries


def is_valid_bibtex_key(key: str) -> bool:
    """Validate a BibTeX citation key.

    Valid keys: alphanumeric plus hyphen, underscore, colon, period, slash.
    Must be 2-80 chars. No spaces, no quotes, no prose fragments.
    """
    if not key or len(key) < 2 or len(key) > 80:
        return False
    if not re.match(r'^[a-zA-Z0-9_:.\-/]+$', key):
        return False
    # Reject keys with too many consecutive lowercase letters (likely prose)
    if len(key) > 40 and not re.search(r'\d', key):
        return False
    return True


def _bibtex_entry_has_required_fields(entry: str) -> bool:
    """Check that a BibTeX entry has at minimum author and title fields."""
    lower = entry.lower()
    has_author = bool(re.search(r'author\s*=', lower))
    has_title = bool(re.search(r'title\s*=', lower))
    return has_author and has_title


def dedup_bibtex(entries: list[str]) -> str:
    """Deduplicate BibTeX entries by cite key, return combined .bib content.

    Rejects entries with invalid cite keys or missing required fields.
    """
    seen = {}
    rejected = 0
    for entry in entries:
        match = re.match(r"@\w+\{([^,]+),", entry)
        if match:
            key = match.group(1).strip()
            if not is_valid_bibtex_key(key):
                rejected += 1
                continue
            if not _bibtex_entry_has_required_fields(entry):
                rejected += 1
                continue
            if key not in seen:
                seen[key] = entry
    if rejected:
        print(f"[dedup_bibtex] Rejected {rejected} invalid BibTeX entries")
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
