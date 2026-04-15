"""Utility functions for the Autonomous Scientist agent."""

import json
import os
import re
import yaml
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

try:
    import tiktoken
    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _TIKTOKEN_AVAILABLE = False


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
# Keys marked [REQUIRED] must be set — the agent cannot function without them.
API_KEY_REGISTRY = {
    # --- REQUIRED ---
    "OPENROUTER_API_KEY": "[REQUIRED] Core LLM inference — every node calls the LLM through OpenRouter; without this key the agent cannot run at all",
    "HF_TOKEN":           "[REQUIRED] Hugging Face Hub — needed for tooluniverse skill (model/dataset discovery); without this key tooluniverse cannot be used",
    "GITHUB_TOKEN":       "[REQUIRED] GitHub API — needed for github-mining skill (code/repo search); without this key github-mining cannot be used",
    # --- OPTIONAL ---
    "OPENAI_API_KEY":     "Required by paper-2-web (HTML/video/poster export)",
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

# --- LLM Configuration (read from env, fall back to defaults) ---
def _get_model() -> str:
    return os.environ.get("MODEL_NAME", "z-ai/glm-5")

def _get_input_cost() -> float:
    return float(os.environ.get("INPUT_TOKEN_COST_PER_MILLION", "0.95"))

def _get_output_cost() -> float:
    return float(os.environ.get("OUTPUT_TOKEN_COST_PER_MILLION", "2.55"))

def _get_base_url() -> str:
    return os.environ.get("INFERENCE_BASE_URL", "https://openrouter.ai/api/v1")


# --- Token counting ---
_tiktoken_enc = None

def count_tokens(text: str) -> int:
    """Count tokens in text using tiktoken (cl100k_base encoding).

    Falls back to rough word-based estimate if tiktoken is not installed.
    """
    global _tiktoken_enc
    if _TIKTOKEN_AVAILABLE:
        if _tiktoken_enc is None:
            _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
        return len(_tiktoken_enc.encode(text))
    # Fallback: ~1.3 tokens per word (rough estimate)
    return int(len(text.split()) * 1.3)


def estimate_calls_remaining(
    budget_remaining: float,
    cost_log: list = None,
    avg_prompt_tokens: int = 500,
    avg_output_tokens: int = 300,
) -> int:
    """Estimate how many LLM calls remain given current budget.

    If cost_log is provided, derives the average cost-per-call from actual
    observed usage rather than the hardcoded defaults.
    """
    if cost_log:
        real_calls = [e for e in cost_log if e.get("input_tokens", 0) > 0]
        if real_calls:
            avg_prompt_tokens = int(sum(e["input_tokens"] for e in real_calls) / len(real_calls))
            avg_output_tokens = int(sum(e["output_tokens"] for e in real_calls) / len(real_calls))
    cost_per_call = (
        avg_prompt_tokens * _get_input_cost() / 1_000_000
        + avg_output_tokens * _get_output_cost() / 1_000_000
    )
    if cost_per_call <= 0:
        return 0
    return max(0, int(budget_remaining / cost_per_call))


# Persistent system prompt injected into every LLM call
_SYSTEM_PROMPT_TEMPLATE = (
    "You are an autonomous research assistant. "
    "Your final goal is to generate a high-quality technical report on the assigned topic. "
    "~{calls} LLM calls remaining. "
    "Be concise and prioritise information density. "
    "Every token costs money — avoid padding, repetition, or lengthy preambles. "
    "IMPORTANT: Respond with plain text only. Do NOT use tool calls, function calls, "
    "XML tags, or any structured invocation syntax. Write your answer directly as text."
)


def get_client() -> OpenAI:
    """Create OpenAI-compatible client from env config."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENROUTER_API_KEY not found.\n"
            "Add to your .env file:\n"
            "  OPENROUTER_API_KEY=sk-or-v1-your-key-here\n"
            "Get one at https://openrouter.ai/keys"
        )
    return OpenAI(
        base_url=_get_base_url(),
        api_key=api_key,
    )


def call_llm(
    prompt: str,
    system: str = None,
    budget_remaining: float = None,
    allow_tools: bool = False,
    cost_log: list = None,
) -> tuple[str, dict]:
    """Call the LLM and return (response_text, usage_dict).

    Injects a budget-aware system prompt. If `system` is provided it is
    appended after the injected preamble so callers can still pass node-
    specific instructions.

    allow_tools: set True only for skill execution steps where the model
    may legitimately invoke tools (e.g. Bash, web search). All planning,
    writing, and review calls must leave this False so tool-calling models
    respond with plain text instead of tool invocations.

    usage_dict keys: input_tokens, output_tokens, cost, estimated_input_tokens
    """
    # Build system message with budget context
    calls_left = estimate_calls_remaining(budget_remaining or 0, cost_log=cost_log)
    budget_ctx = _SYSTEM_PROMPT_TEMPLATE.format(calls=calls_left)
    full_system = budget_ctx
    if system:
        full_system = budget_ctx + "\n\n" + system

    # Estimate tokens before API call (for logging/debugging)
    est_input = count_tokens(full_system) + count_tokens(prompt)

    client = get_client()
    messages = [
        {"role": "system", "content": full_system},
        {"role": "user", "content": prompt},
    ]

    kwargs = {"model": _get_model(), "messages": messages}
    if not allow_tools:
        kwargs["tool_choice"] = "none"

    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as e:
        # Some backends reject tool_choice="none" when no tools are defined.
        if not allow_tools and ("tool_choice" in str(e).lower() or getattr(getattr(e, "response", None), "status_code", None) == 400):
            response = client.chat.completions.create(model=_get_model(), messages=messages)
        else:
            raise

    choice = response.choices[0]
    text = choice.message.content or ""
    if not allow_tools:
        # Planning/writing calls must not contain tool invocations — strip any that leaked.
        if not text and choice.message.tool_calls:
            # Model ignored tool_choice; salvage argument blobs as raw text.
            text = "\n".join(
                tc.function.arguments for tc in choice.message.tool_calls
                if tc.function and tc.function.arguments
            )
        # Strip XML-style tool-call tags some models bleed into content (e.g. minimax).
        text = re.sub(r"<[a-zA-Z0-9_:]+:tool_call>.*?</[a-zA-Z0-9_:]+:tool_call>", "", text, flags=re.DOTALL)
        text = re.sub(r"</?[a-zA-Z0-9_:]+:tool_call[^>]*>", "", text)
    usage = response.usage
    input_tokens = usage.prompt_tokens if usage else est_input
    output_tokens = usage.completion_tokens if usage else count_tokens(text)
    cost = (
        input_tokens * _get_input_cost() / 1_000_000
        + output_tokens * _get_output_cost() / 1_000_000
    )

    return text, {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost": cost,
        "estimated_input_tokens": est_input,
    }


# Tool definition exposed to the model for Bash execution
_BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Execute a bash or python command in the task working directory. Use for data collection, computation, file I/O, and API calls.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to run.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 60, max 300).",
                    "default": 60,
                },
            },
            "required": ["command"],
        },
    },
}


def _execute_tool_call(tool_name: str, arguments: dict, cwd: str) -> str:
    """Execute a model-requested tool call and return its output as a string."""
    import subprocess
    if tool_name == "bash":
        command = arguments.get("command", "")
        timeout = min(int(arguments.get("timeout", 60)), 300)
        try:
            r = subprocess.run(
                command, shell=True, cwd=cwd,
                capture_output=True, text=True,
                errors="replace", timeout=timeout,
                env={**os.environ},
            )
            out = r.stdout[:4000]
            err = r.stderr[:1000]
            result = f"exit={r.returncode}"
            if out:
                result += f"\n{out}"
            if err:
                result += f"\n[stderr] {err}"
            return result
        except subprocess.TimeoutExpired:
            return f"[ERROR] Command timed out after {timeout}s"
        except Exception as e:
            return f"[ERROR] {e}"
    return f"[ERROR] Unknown tool: {tool_name}"


def call_llm_with_tools(
    prompt: str,
    system: str = None,
    budget_remaining: float = None,
    cwd: str = ".",
    max_tool_rounds: int = 8,
    cost_log: list = None,
) -> tuple[str, dict]:
    """Call the LLM with Bash tool access and feed execution results back until done.

    The model may call the bash tool repeatedly. Each result is appended to the
    conversation so the model can react to errors and retry. The loop ends when
    the model stops calling tools or max_tool_rounds is reached.

    Returns (final_text, aggregated_usage_dict).
    """
    calls_left = estimate_calls_remaining(budget_remaining or 0, cost_log=cost_log)
    budget_ctx = _SYSTEM_PROMPT_TEMPLATE.format(calls=calls_left)
    full_system = budget_ctx
    if system:
        full_system = budget_ctx + "\n\n" + system

    client = get_client()
    messages = [
        {"role": "system", "content": full_system},
        {"role": "user", "content": prompt},
    ]

    total_input = count_tokens(full_system) + count_tokens(prompt)
    total_output = 0
    total_cost = 0.0
    final_text = ""

    for round_i in range(max_tool_rounds):
        try:
            response = client.chat.completions.create(
                model=_get_model(),
                messages=messages,
                tools=[_BASH_TOOL],
                tool_choice="auto",
            )
        except Exception as e:
            # Backend doesn't support tools — fall back to plain call_llm
            if "tool" in str(e).lower() or getattr(getattr(e, "response", None), "status_code", None) == 400:
                plain_text, plain_usage = call_llm(prompt, system=system, budget_remaining=budget_remaining)
                return plain_text, plain_usage
            raise

        usage = response.usage
        if usage:
            total_input += usage.prompt_tokens
            total_output += usage.completion_tokens
            total_cost += (
                usage.prompt_tokens * _get_input_cost() / 1_000_000
                + usage.completion_tokens * _get_output_cost() / 1_000_000
            )

        choice = response.choices[0]
        msg = choice.message

        # Accumulate any text content
        if msg.content:
            final_text = msg.content

        # If no tool calls, model is done
        if not msg.tool_calls:
            break

        # Append assistant message (with tool_calls) to history
        messages.append(msg)

        # Execute each tool call and append results
        tool_results = []
        for tc in msg.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, AttributeError):
                args = {}
            output = _execute_tool_call(tc.function.name, args, cwd)
            print(f"[tool:{tc.function.name}] {str(args.get('command',''))[:60]} → {output[:80]}")
            tool_results.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": output,
            })

        messages.extend(tool_results)

        if round_i == max_tool_rounds - 1:
            print(f"[call_llm_with_tools] Reached max_tool_rounds={max_tool_rounds}, stopping.")

    return final_text, {
        "input_tokens": total_input,
        "output_tokens": total_output,
        "cost": total_cost,
        "estimated_input_tokens": total_input,
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


def filter_skill_index(skills_dir: str, index: dict[str, str], api_keys: dict[str, bool]) -> dict[str, str]:
    """Remove skills whose required-keys are not available.

    Reads each SKILL.md frontmatter (only the first few lines) to check
    required-keys. Skills with no required-keys are always included.
    """
    filtered = {}
    for skill_id, desc in index.items():
        skill_file = Path(skills_dir) / skill_id / "SKILL.md"
        required = []
        if skill_file.exists():
            raw = skill_file.read_text(encoding="utf-8")
            fm = re.match(r'^---\s*\n(.*?)\n---\s*\n', raw, re.DOTALL)
            if fm:
                try:
                    meta = yaml.safe_load(fm.group(1)) or {}
                    keys = meta.get("required-keys", [])
                    if isinstance(keys, str):
                        keys = [k.strip() for k in keys.split(",")]
                    required = keys
                except yaml.YAMLError:
                    pass
        if all(api_keys.get(k) for k in required):
            filtered[skill_id] = desc
    return filtered


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

    # Normalize required-keys to a list of strings
    keys = metadata.get("required-keys", [])
    if isinstance(keys, str):
        keys = [k.strip() for k in keys.split(",")]
    metadata["required-keys"] = keys

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


def track_cost(shared: dict, step: str, usage: dict):
    """Append cost to shared ledger and decrement remaining budget."""
    shared.setdefault("cost_log", []).append({"step": step, **usage})
    shared["budget_remaining"] = shared.get("budget_remaining", 0) - usage["cost"]
