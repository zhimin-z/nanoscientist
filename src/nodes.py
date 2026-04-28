"""PocketFlow nodes for the Autonomous Scientist agent.

Pipeline:
  Initializer
    → LiteratureReviewLoop  (terminates on quality gate or budget)
    → ExperimentationLoop   (terminates on quality gate or budget)
    → WritingLoop           (terminates on quality gate or budget)
    → CompilingLoop (CompileTeX ↔ FixTeX)
    → Finisher
"""

import asyncio
import json
import os
import re
import subprocess
import uuid
from pathlib import Path

from pocketflow import Node, AsyncNode

from .utils import (
    call_llm_async,
    call_llm_with_tools_async,
    load_skill_content,
    format_skill_index,
    parse_yaml_response,
    extract_bibtex,
    dedup_bibtex,
    track_cost,
    estimate_calls_remaining,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_DEFAULTS = {
    "BUDGET_RESERVE_RATIO":      "0.05",   # stop loop if remaining < 5% of original
    "WRITE_RESERVE_RATIO":       "0.02",   # skip section write if below 2%
    "REVIEW_RESERVE_RATIO":      "0.01",   # skip review if below 1%
    "CODE_EXEC_TIMEOUT":         "300",
    "LATEX_COMPILE_TIMEOUT":     "60",
    "SKILL_CONTENT_LIMIT":       "1500",
    "ARTIFACT_CONTEXT_CHARS":    "2500",
    "PRIOR_SECTION_CHARS":       "800",
    "SALVAGE_CONTEXT_CHARS":     "3000",
    "TITLE_TOPIC_CHARS":         "2000",
    "MIN_SECTION_LENGTH":        "100",
    "TITLE_MAX_WORDS":           "15",
    "MIN_CALLS_TO_CONTINUE":     "3",
}


def _cfg(key: str, cast=float) -> float:
    return cast(os.environ.get(key, _DEFAULTS[key]))


BUDGET_RESERVE_RATIO    = _cfg("BUDGET_RESERVE_RATIO")
WRITE_RESERVE_RATIO     = _cfg("WRITE_RESERVE_RATIO")
REVIEW_RESERVE_RATIO    = _cfg("REVIEW_RESERVE_RATIO")
CODE_EXEC_TIMEOUT       = _cfg("CODE_EXEC_TIMEOUT",     int)
LATEX_COMPILE_TIMEOUT   = _cfg("LATEX_COMPILE_TIMEOUT", int)
SKILL_CONTENT_LIMIT     = _cfg("SKILL_CONTENT_LIMIT",   int)
ARTIFACT_CONTEXT_CHARS  = _cfg("ARTIFACT_CONTEXT_CHARS",int)
PRIOR_SECTION_CHARS     = _cfg("PRIOR_SECTION_CHARS",   int)
SALVAGE_CONTEXT_CHARS   = _cfg("SALVAGE_CONTEXT_CHARS", int)
TITLE_TOPIC_CHARS       = _cfg("TITLE_TOPIC_CHARS",     int)
MIN_SECTION_LENGTH      = _cfg("MIN_SECTION_LENGTH",    int)
TITLE_MAX_WORDS         = _cfg("TITLE_MAX_WORDS",       int)
MIN_CALLS_TO_CONTINUE   = _cfg("MIN_CALLS_TO_CONTINUE", int)

FULL_PAPER_SECTIONS = [
    "abstract", "introduction", "background", "methods",
    "results", "discussion", "limitations", "conclusion",
]

LATEX_SKELETON = r"""\documentclass[11pt]{article}
\usepackage[margin=1in]{geometry}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\graphicspath{{./figures/}}
\usepackage{booktabs}
\usepackage{float}
\usepackage{microtype}
\usepackage[hyphens]{url}
\usepackage[colorlinks=true,linkcolor=blue,citecolor=blue,breaklinks=true]{hyperref}
\usepackage[numbers,sort&compress]{natbib}
\usepackage{xcolor}

\title{%% TITLE %%}
\author{Autonomous Scientist Agent}
\date{\today}

\begin{document}
\maketitle

\begin{abstract}
%% ABSTRACT %%
\end{abstract}

%% BODY %%

\bibliographystyle{unsrtnat}
\bibliography{references}

\end{document}
"""


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _section_order(shared: dict) -> list[str]:
    return FULL_PAPER_SECTIONS


def _budget_ok(shared: dict, reserve_ratio: float = None) -> bool:
    ratio = reserve_ratio if reserve_ratio is not None else BUDGET_RESERVE_RATIO
    budget = shared.get("budget_dollars", 0)
    remaining = shared.get("budget_remaining", 0)
    if remaining < budget * ratio:
        return False
    calls = estimate_calls_remaining(remaining, cost_log=shared.get("cost_log", []))
    return calls >= MIN_CALLS_TO_CONTINUE


def _recent_history(shared: dict, n: int = None) -> str:
    if n is None:
        n = int(os.environ.get("LOOKBACK", "3"))
    history = shared.get("history", [])
    return "\n".join(
        f"- {h['stage']}/{h.get('label','')}: {h['summary'][:120]}"
        + (f" [ERR: {h['error']}]" if h.get("error") else "")
        for h in history[-n:]
    ) or "No history yet."


def _artifact_index(shared: dict) -> str:
    out_dir = Path(shared.get("output_path", ""))
    artifact_dir = out_dir / "artifacts"
    if not artifact_dir.is_dir():
        return "No artifacts yet."
    lines = []
    for f in sorted(artifact_dir.iterdir()):
        if f.suffix == ".md":
            try:
                first = next((l.strip() for l in f.read_text(encoding="utf-8")
                              .splitlines() if l.strip()), "(empty)")
                lines.append(f"- {f.relative_to(out_dir)}: {first[:100]}")
            except Exception:
                lines.append(f"- {f.relative_to(out_dir)}")
    return "\n".join(lines) or "No artifacts yet."


def _existing_files(shared: dict) -> str:
    out_dir = Path(shared.get("output_path", ""))
    lines = []
    for sub in ("data", "figures", "scripts"):
        d = out_dir / sub
        if d.is_dir():
            for f in sorted(d.iterdir()):
                if f.is_file():
                    lines.append(f"- {sub}/{f.name}")
    return "\n".join(lines) if lines else "None yet."


def _data_summary(shared: dict) -> str:
    out_dir = Path(shared.get("output_path", ""))
    data_dir = out_dir / "data"
    if not data_dir.is_dir():
        return ""
    lines = ["## Data files (USE THESE EXACT NUMBERS — do not invent statistics)"]
    found_any = False
    for f in sorted(data_dir.iterdir()):
        if not f.is_file():
            continue
        if f.suffix.lower() == ".json":
            try:
                data = json.loads(f.read_text(encoding="utf-8", errors="replace"))
                stats = []
                def _flatten(obj, prefix=""):
                    if isinstance(obj, dict):
                        for k, v in list(obj.items())[:30]:
                            _flatten(v, f"{prefix}{k}.")
                    elif isinstance(obj, list):
                        stats.append(f"{prefix}count={len(obj)}")
                        if obj and isinstance(obj[0], dict):
                            stats.append(f"{prefix}fields={list(obj[0].keys())[:8]}")
                    elif isinstance(obj, (int, float, str, bool)) and obj is not None:
                        stats.append(f"{prefix[:-1]}={str(obj)[:120]}")
                _flatten(data)
                if stats:
                    lines.append(f"### {f.name}")
                    lines.extend(f"  {s}" for s in stats[:40])
                    found_any = True
            except Exception:
                pass
        elif f.suffix.lower() == ".csv":
            try:
                rows = [r for r in f.read_text(encoding="utf-8", errors="replace").splitlines() if r.strip()]
                if rows:
                    lines.append(f"### {f.name}")
                    lines.append(f"  rows={len(rows)-1}  columns={rows[0][:200]}")
                    found_any = True
            except Exception:
                pass
    return "\n".join(lines) if found_any else ""


def _extend_bibtex(shared: dict, new_entries: list[str]):
    existing_keys = set()
    for e in shared.get("bibtex_entries", []):
        m = re.match(r"@\w+\{([^,]+),", e)
        if m:
            existing_keys.add(m.group(1).strip())
    for entry in new_entries:
        m = re.match(r"@\w+\{([^,]+),", entry)
        if m and m.group(1).strip() not in existing_keys:
            shared.setdefault("bibtex_entries", []).append(entry)
            existing_keys.add(m.group(1).strip())


def _sanitize_section_body(body: str) -> str:
    body = re.sub(r"%%BEGIN BIBTEX%%.*?%%END BIBTEX%%", "", body, flags=re.DOTALL)
    body = re.sub(r"%%BEGIN CODE:\w+%%.*?%%END CODE%%", "", body, flags=re.DOTALL)
    body = re.sub(r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}", "", body, flags=re.DOTALL)
    body = re.sub(r"\\bibliographystyle\{[^}]*\}", "", body)
    body = re.sub(r"\\bibliography\{[^}]*\}", "", body)
    body = re.sub(r"```\w*\n.*?```", "", body, flags=re.DOTALL)
    body = re.sub(r"\\begin\{verbatim\}.*?\\end\{verbatim\}", "", body, flags=re.DOTALL)
    body = re.sub(r"\\documentclass\b.*?\n", "", body)
    body = re.sub(r"\\usepackage\b.*?\n", "", body)
    body = re.sub(r"\\begin\{document\}|\\end\{document\}", "", body)
    body = re.sub(r"\\maketitle\b", "", body)
    return body.strip()


def _run_code_blocks(text: str, label: str, task_dir: Path, shared: dict) -> list[str]:
    for sub in ("data", "figures", "scripts"):
        (task_dir / sub).mkdir(exist_ok=True)
    outputs = []
    for i, (lang, code) in enumerate(
            re.findall(r"%%BEGIN CODE:(\w+)%%(.*?)%%END CODE%%", text, re.DOTALL)):
        code = code.strip()
        if not code:
            continue
        step_num = len(shared.get("history", [])) + 1
        ext = ".py" if lang == "python" else ".sh"
        script = task_dir / "scripts" / f"{step_num:02d}_{label}_{i:02d}{ext}"
        script.write_text(code, encoding="utf-8")
        cmd = ["python", str(script)] if lang == "python" else ["bash", str(script)]
        try:
            r = subprocess.run(cmd, cwd=str(task_dir), capture_output=True,
                               text=True, errors="replace", timeout=CODE_EXEC_TIMEOUT,
                               env={**os.environ, "OUTPUT_DIR": str(task_dir)})
            outputs.append(f"[{script.name}] exit={r.returncode}\n{r.stdout[:3000]}")
            if r.returncode != 0:
                outputs.append(f"[STDERR] {r.stderr[:1000]}")
                print(f"[code] WARNING: {script.name} exited {r.returncode}")
            else:
                print(f"[code] {script.name}: exit={r.returncode}")
        except subprocess.TimeoutExpired:
            outputs.append(f"[{script.name}] TIMEOUT")
        except Exception as e:
            outputs.append(f"[{script.name}] ERROR: {e}")
    return outputs


def _save_artifact(text: str, stage: str, label: str,
                   code_outputs: list[str], usage: dict, shared: dict):
    bib_m = re.search(r"%%BEGIN BIBTEX%%(.*?)%%END BIBTEX%%", text, re.DOTALL)
    if bib_m:
        main = text[:bib_m.start()].strip()
        entries = re.findall(r"(@\w+\{[^@]+)", bib_m.group(1), re.DOTALL)
    else:
        main, entries = extract_bibtex(text)
    entries = [e.strip() for e in entries if e.strip()]
    main = re.sub(r"%%BEGIN CODE:\w+%%.*?%%END CODE%%", "", main, flags=re.DOTALL).strip()
    if code_outputs:
        main += "\n\n## Code Output\n" + "\n".join(code_outputs)

    shared["artifacts"][label] = (shared["artifacts"].get(label, "") + "\n\n" + main).strip()
    _extend_bibtex(shared, entries)

    out_dir = Path(shared["output_path"])
    (out_dir / "artifacts").mkdir(exist_ok=True)
    step_num = len(shared["history"]) + 1
    (out_dir / "artifacts" / f"{step_num:02d}_{stage}_{label}.md").write_text(main, encoding="utf-8")
    if entries:
        (out_dir / "artifacts" / f"{step_num:02d}_{stage}_{label}.bib").write_text(
            "\n\n".join(entries) + "\n", encoding="utf-8")

    summary = (main[:200] + "...").replace("\n", " ") if len(main) > 200 else main[:200].replace("\n", " ")
    shared["history"].append({
        "step": step_num, "stage": stage, "label": label,
        "summary": summary, "cost": usage["cost"],
        "error": None,
    })
    (Path(shared["output_path"]) / "history.json").write_text(
        json.dumps(shared["history"], indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[artifact] {stage}/{label}: {len(entries)} bibtex, ${usage['cost']:.4f}")


async def _generate_workflow_diagram_async(shared: dict):
    out_dir = Path(shared["output_path"])
    dest = out_dir / "figures" / "workflow.png"
    if dest.exists():
        return
    project_root = Path(__file__).resolve().parents[1]
    generate_script = project_root / "skills" / "study-workflow" / "scripts" / "generate.py"
    if not generate_script.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)

    topic = shared.get("topic", "research")[:80]
    lit_labels  = [h["label"][:55] for h in shared.get("history", []) if h.get("stage") == "literature"][:6]
    exp_labels  = [h["label"][:55] for h in shared.get("history", []) if h.get("stage") == "experiment"][:4]
    write_labels = list(shared.get("section_bodies", {}).keys())[:5]
    research_steps = (lit_labels + exp_labels) or ["Literature Survey", "Analysis"]
    write_steps    = write_labels or ["Introduction", "Methods", "Results", "Conclusion"]

    def _run():
        return subprocess.run(
            ["python", str(generate_script),
             "--output", str(dest),
             "--research-steps", json.dumps(research_steps),
             "--write-steps", json.dumps(write_steps),
             "--topic", topic],
            capture_output=True, text=True, errors="replace",
            timeout=int(os.environ.get("LATEX_COMPILE_TIMEOUT", "60")),
            env={**os.environ},
        )

    try:
        r = await asyncio.get_event_loop().run_in_executor(None, _run)
        if r.returncode == 0 and dest.exists():
            print(f"[workflow] diagram saved → {dest}")
        else:
            print(f"[workflow] generate.py failed (exit={r.returncode}): {r.stderr[:300]}")
    except Exception as e:
        print(f"[workflow] diagram generation error: {e}")


# ---------------------------------------------------------------------------
# Per-call context builder
# ---------------------------------------------------------------------------

def _build_context(shared: dict, stage_goal: str, skill_index: dict) -> str:
    """Assemble the user-turn prompt delivered to every loop LLM call."""
    lookback = int(os.environ.get("LOOKBACK", "3"))
    calls_left = estimate_calls_remaining(
        shared.get("budget_remaining", 0),
        cost_log=shared.get("cost_log", []),
    )
    skills_block = format_skill_index(skill_index) if skill_index else "None available."
    return (
        f"## Stage goal\n{stage_goal}\n\n"
        f"## Topic\n{shared['topic']}\n\n"
        f"## Budget remaining\n${shared.get('budget_remaining', 0):.4f} "
        f"(~{calls_left} calls left)\n\n"
        f"## Available skills\n{skills_block}\n\n"
        f"## Last {lookback} steps\n{_recent_history(shared, lookback)}\n\n"
        f"## Artifacts collected\n{_artifact_index(shared)}"
    )


# ---------------------------------------------------------------------------
# Loop core: ask LLM what to do next, execute it, check termination
# ---------------------------------------------------------------------------

_LOOP_SYSTEM = (
    "You are an autonomous research agent. "
    "Each turn you decide the single most valuable next action for your current stage. "
    "You may call a skill (using the bash tool to invoke its script), "
    "or declare the stage goal met. "
    "Be concise — every token costs money."
)

_SKILL_SYSTEM = (
    "You are an autonomous research executor. "
    "Use the bash tool to complete the assigned research step. "
    "Save data to the OUTPUT_DIR/data/ directory and figures to OUTPUT_DIR/figures/. "
    "When done, write a concise summary of findings. "
    "Cite sources with %%BEGIN BIBTEX%% ... %%END BIBTEX%%."
)

_WRITE_SYSTEM = (
    "You are an academic writing agent. "
    "Write the assigned paper section as compilable LaTeX. "
    "Be precise, citation-backed, and follow all LaTeX rules given."
)

_REVIEW_SYSTEM = (
    "You are a rigorous peer reviewer. "
    "Evaluate the draft against NeurIPS/ICML/ICLR/ACL standards. "
    "Return a structured YAML verdict."
)


async def _decide_next_action(shared: dict, stage: str, stage_goal: str,
                               skill_index: dict) -> tuple[str, dict]:
    """Ask the LLM what to do next in this loop iteration.

    Returns (action_yaml_text, usage_dict).
    action YAML shape:
      action: skill | done
      skill: <skill-name>       # when action==skill
      reason: <one-line>
    """
    context = _build_context(shared, stage_goal, skill_index)
    prompt = (
        f"{context}\n\n"
        "Decide the single best next action for this stage.\n"
        "Return YAML only:\n"
        "```yaml\n"
        "action: skill   # or: done\n"
        "skill: <skill-name>   # required when action==skill\n"
        "reason: <one sentence>\n"
        "```\n"
        "Choose `done` when the stage goal is sufficiently achieved "
        "or remaining budget is insufficient to continue."
    )
    text, usage = await call_llm_async(
        prompt,
        system=_LOOP_SYSTEM,
        budget_remaining=shared.get("budget_remaining", 0),
        cost_log=shared.get("cost_log"),
    )
    track_cost(shared, f"{stage}:decide", usage)
    return text, usage


async def _execute_skill(skill_name: str, stage: str, shared: dict):
    """Load a skill and run it with or without bash tools."""
    skill_content, meta = load_skill_content(shared["skills_dir"], skill_name)
    can_execute = "Bash" in meta.get("allowed-tools", [])
    abs_out = str(Path(shared["output_path"]).resolve())

    prompt = (
        f"## Topic\n{shared['topic']}\n\n"
        f"## Skill: {skill_name}\n"
        f"## Skill instructions\n{skill_content[:SKILL_CONTENT_LIMIT]}\n\n"
        f"## Working directory (ABSOLUTE): {abs_out}\n"
        f"## Already produced files (DO NOT recreate):\n{_existing_files(shared)}\n\n"
        "## Environment\n"
        "All API keys (OPENROUTER_API_KEY, PERPLEXITY_API_KEY, GITHUB_TOKEN, "
        "HF_TOKEN, OPENAI_API_KEY, etc.) are pre-loaded from .env — "
        "use them directly in bash commands without reading .env.\n\n"
        "CRITICAL rules:\n"
        f"- ALL bash commands run inside {abs_out}.\n"
        f"- Save data to `{abs_out}/data/` — use ABSOLUTE paths.\n"
        f"- Save figures to `{abs_out}/figures/` — use ABSOLUTE paths.\n"
        "- No plt.show(). Use plt.savefig(...) explicitly.\n"
        "- NEVER use `sleep`, `npx`, `npm`, or `node`.\n"
        "- Write large files with Python open(), not shell heredocs.\n\n"
        "After completing, summarise findings and append citations:\n"
        "%%BEGIN BIBTEX%%\n"
        "@article{key, author={...}, title={...}, journal={...}, year={YYYY}}\n"
        "%%END BIBTEX%%\n\n"
        "CITATION RULES:\n"
        "- Keys MUST follow author+year format (e.g. smith2023attention).\n"
        "- Every entry MUST have author= and title= fields.\n"
        "SUMMARY RULES:\n"
        "- NEVER mention absolute file paths in the summary.\n"
        "- Refer to files by short relative name only.\n"
        f"## Recent context\n{_recent_history(shared, 5)}"
    )

    if can_execute:
        text, usage = await call_llm_with_tools_async(
            prompt,
            system=_SKILL_SYSTEM,
            budget_remaining=shared.get("budget_remaining", 0),
            cwd=abs_out,
            cost_log=shared.get("cost_log"),
        )
        if usage.get("tool_rounds_exhausted"):
            print(f"[{stage}] {skill_name}: tool rounds exhausted — salvaging.")
            salvage_text, salvage_usage = await call_llm_async(
                f"Summarise ALL findings collected so far in this step.\n"
                f"Step: {skill_name}\nPartial output:\n{text[:SALVAGE_CONTEXT_CHARS]}\n\n"
                "Write 200–400 words of findings, then cite:\n"
                "%%BEGIN BIBTEX%%\n@article{key, author={...}, title={...}, year={YYYY}}\n%%END BIBTEX%%",
                budget_remaining=shared.get("budget_remaining", 0),
                cost_log=shared.get("cost_log"),
            )
            track_cost(shared, f"{stage}:salvage:{skill_name}", salvage_usage)
            text = text + "\n\n## SALVAGE SUMMARY\n" + salvage_text
    else:
        text, usage = await call_llm_async(
            prompt,
            system=_SKILL_SYSTEM,
            budget_remaining=shared.get("budget_remaining", 0),
            cost_log=shared.get("cost_log"),
        )

    track_cost(shared, f"{stage}:{skill_name}", usage)
    code_outputs = _run_code_blocks(text, skill_name, Path(shared["output_path"]), shared)
    _save_artifact(text, stage, skill_name, code_outputs, usage, shared)
    print(f"[{stage}] {skill_name} done. Budget: ${shared['budget_remaining']:.4f}")


async def _quality_gate(shared: dict, stage: str, stage_goal: str) -> tuple[bool, str]:
    """Ask the LLM whether the stage goal has been met.

    Returns (accepted, feedback_text).
    """
    prompt = (
        f"## Stage goal\n{stage_goal}\n\n"
        f"## Topic\n{shared['topic']}\n\n"
        f"## Artifacts collected\n{_artifact_index(shared)}\n\n"
        f"## Steps executed in this stage\n"
        + "\n".join(
            f"- {h['label']}: {h['summary'][:120]}"
            for h in shared.get("history", [])
            if h.get("stage") == stage
        ) + "\n\n"
        "Has the stage goal been sufficiently achieved?\n"
        "Return YAML only:\n"
        "```yaml\n"
        "accepted: true   # or false\n"
        "feedback: <one-sentence reason>\n"
        "```"
    )
    text, usage = await call_llm_async(
        prompt,
        system=_REVIEW_SYSTEM,
        budget_remaining=shared.get("budget_remaining", 0),
        cost_log=shared.get("cost_log"),
    )
    track_cost(shared, f"{stage}:quality_gate", usage)
    parsed = parse_yaml_response(text)
    if isinstance(parsed, dict):
        return bool(parsed.get("accepted", False)), str(parsed.get("feedback", ""))
    return False, text[:200]


async def _run_loop(shared: dict, stage: str, stage_goal: str,
                    skill_filter_fn=None) -> str:
    """Run one research stage as an autonomous loop.

    Terminates when:
    - LLM decides action==done, OR
    - quality gate passes (checked every iteration), OR
    - budget insufficient to continue.

    Returns the exit reason string for logging.
    """
    skill_index = {k: v for k, v in shared.get("skill_index", {}).items()
                   if skill_filter_fn is None or skill_filter_fn(k)}
    iteration = 0
    max_iter = int(os.environ.get("MAX_LOOP_ITERATIONS", "20"))

    while iteration < max_iter:
        iteration += 1

        if not _budget_ok(shared, BUDGET_RESERVE_RATIO):
            print(f"[{stage}] Budget exhausted — exiting loop.")
            return "budget_exhausted"

        # Decide next action
        decision_text, _ = await _decide_next_action(shared, stage, stage_goal, skill_index)
        decision = parse_yaml_response(decision_text)

        if not isinstance(decision, dict):
            print(f"[{stage}] Could not parse decision — continuing with quality check.")
            decision = {"action": "done", "reason": "parse failure"}

        action = decision.get("action", "done")
        print(f"[{stage}] iter={iteration} action={action} reason={decision.get('reason','')[:80]}")

        if action == "skill":
            skill_name = decision.get("skill", "")
            if skill_name not in skill_index:
                print(f"[{stage}] Unknown skill '{skill_name}' — skipping.")
            else:
                await _execute_skill(skill_name, stage, shared)

        # Quality gate after every action (including after skill execution)
        accepted, feedback = await _quality_gate(shared, stage, stage_goal)
        print(f"[{stage}] quality_gate: accepted={accepted} — {feedback[:100]}")
        if accepted:
            return "goal_achieved"

        if action == "done":
            # LLM said done but gate didn't accept — one more iteration then exit
            print(f"[{stage}] LLM declared done but quality gate not met — exiting.")
            return "llm_done"

    print(f"[{stage}] Max iterations reached.")
    return "max_iterations"


# ---------------------------------------------------------------------------
# Writing helpers
# ---------------------------------------------------------------------------

async def _write_section(section: str, shared: dict):
    cite_keys = [m.group(1).strip()
                 for e in shared.get("bibtex_entries", [])
                 for m in [re.match(r"@\w+\{([^,]+),", e)] if m]
    artifact_text = "\n\n".join(f"### {k}\n{v[:ARTIFACT_CONTEXT_CHARS]}"
                                for k, v in shared.get("artifacts", {}).items())
    prior_text = ("\n\n## Prior sections\n" +
                  "\n\n".join(f"### {s}\n{b[:PRIOR_SECTION_CHARS]}"
                              for s, b in shared.get("section_bodies", {}).items())
                  if shared.get("section_bodies") else "")
    out_dir = Path(shared["output_path"])
    figures_dir = out_dir / "figures"
    figure_files = ([f.name for f in sorted(figures_dir.iterdir())
                     if f.suffix.lower() in (".png", ".pdf", ".jpg", ".jpeg")]
                    if figures_dir.is_dir() else [])
    figures_used = set(shared.get("figures_used", []))
    fresh_figures = [f for f in figure_files if f not in figures_used]
    used_figures  = [f for f in figure_files if f in figures_used]
    figures_block = ""
    if figure_files:
        lines = ["\n\n## Figures"]
        if fresh_figures:
            lines.append("### Available (not yet used — PREFER THESE):")
            lines.extend(f"- {f}" for f in fresh_figures)
        if used_figures:
            lines.append("### Already used (avoid reusing):")
            lines.extend(f"- {f}" for f in used_figures)
        lines.append("\nUse this pattern:\n"
                     r"```latex" "\n"
                     r"\begin{figure}[htbp]" "\n"
                     r"\centering" "\n"
                     r"\includegraphics[width=0.9\textwidth]{figures/<filename>}" "\n"
                     r"\caption{<caption>}" "\n"
                     r"\label{fig:<label>}" "\n"
                     r"\end{figure}" "\n"
                     "```")
        figures_block = "\n".join(lines)
    data_block = _data_summary(shared)
    if data_block:
        data_block = "\n\n" + data_block

    text, usage = await call_llm_async(
        f"""Write the **{section}** section of a research paper as compilable LaTeX.

## Topic: {shared["topic"]}
## Artifacts
{artifact_text}{prior_text}{figures_block}{data_block}

## BibTeX keys: {", ".join(cite_keys) or "No citations yet."}

## LaTeX rules
- Output ONLY this section's LaTeX (\\section{{...}} onward). No preamble, no \\documentclass.
- Use \\cite{{key}} only from the BibTeX keys listed above.
- Do NOT include \\bibliography or \\bibliographystyle.
- Escape \\%, \\&, \\#, \\$, \\_ in text mode.
- Underscored identifiers must appear in \\texttt{{}} or math mode.
- Unicode symbols (≥, —) MUST be replaced: $\\geq$, --- or \\textemdash{{}}.
- Tables: \\resizebox{{\\textwidth}}{{!}}{{...}} around wide tabular.
- Figures: [htbp] placement, \\includegraphics[width=0.9\\textwidth]{{figures/<filename>}}.
- Workflow figure: include workflow.png ONCE in Introduction if available.
- NEVER output raw markdown fences or \\begin{{verbatim}} blocks.
- To generate a figure use ONLY:
  %%BEGIN CODE:python%%
  import os, matplotlib.pyplot as plt
  out = os.environ.get("OUTPUT_DIR", ".")
  os.makedirs(f"{{out}}/figures", exist_ok=True)
  plt.savefig(f"{{out}}/figures/<name>.png", dpi=300, bbox_inches="tight")
  plt.close()
  %%END CODE%%

## Writing rules
- Active voice. Formal academic tone. Claim–evidence–commentary per paragraph.
- Banned: "systematic", "comprehensive", "robust", "novel", "leveraging", "facilitating",
  "underpinning", "delve", "landscape", "recurring", "actionable", "end-to-end", "paving the way".
- No em dashes or semicolons in prose.
- No vague quantifiers — use exact counts.
- Introduction must end with numbered contributions: "(1) ..., (2) ..., (3) ..."
- Abstract: 150–250 words, every claim must have a number.
- Conclusion must NOT repeat the abstract.
- Limitations section MUST appear before Conclusion.

%%BEGIN SECTION%%
\\section{{{section.title()}}}
...
%%END SECTION%%

%%BEGIN BIBTEX%%
@article{{key, ...}}
%%END BIBTEX%%""",
        system=_WRITE_SYSTEM,
        budget_remaining=shared.get("budget_remaining", 0),
        cost_log=shared.get("cost_log"),
    )
    track_cost(shared, f"writing:{section}", usage)

    sec_m = re.search(r"%%BEGIN SECTION%%(.*?)%%END SECTION%%", text, re.DOTALL)
    raw_section = sec_m.group(1) if sec_m else text
    code_outputs = _run_code_blocks(raw_section, f"write:{section}", out_dir, shared)
    if code_outputs:
        print(f"[WritingLoop] '{section}': ran {len(code_outputs)} code block(s)")

    body = _sanitize_section_body(raw_section)
    bib_m = re.search(r"%%BEGIN BIBTEX%%(.*?)%%END BIBTEX%%", text, re.DOTALL)
    if bib_m:
        new_entries = [e.strip() for e in re.findall(r"(@\w+\{[^@]+)", bib_m.group(1), re.DOTALL) if e.strip()]
        _extend_bibtex(shared, new_entries)

    if len(body.strip()) < MIN_SECTION_LENGTH:
        print(f"[WritingLoop] '{section}' too short — retrying.")
        retry_text, retry_usage = await call_llm_async(
            f"IMPORTANT: output the {section} section content between the markers.\n\n"
            f"## Topic: {shared['topic']}\n"
            f"## Artifacts\n{artifact_text[:ARTIFACT_CONTEXT_CHARS]}\n"
            f"## BibTeX keys: {', '.join(cite_keys) or 'None'}\n\n"
            f"%%BEGIN SECTION%%\n\\section{{{section.title()}}}\n...content...\n%%END SECTION%%",
            system=_WRITE_SYSTEM,
            budget_remaining=shared.get("budget_remaining", 0),
            cost_log=shared.get("cost_log"),
        )
        track_cost(shared, f"writing:{section}:retry", retry_usage)
        retry_sec_m = re.search(r"%%BEGIN SECTION%%(.*?)%%END SECTION%%", retry_text, re.DOTALL)
        retry_body = _sanitize_section_body(retry_sec_m.group(1) if retry_sec_m else retry_text)
        if len(retry_body.strip()) > len(body.strip()):
            body = retry_body

    shared.setdefault("section_bodies", {})[section] = body
    if section not in shared.get("sections_written", []):
        shared.setdefault("sections_written", []).append(section)
    for fname in re.findall(r"\\includegraphics[^{]*\{figures/([^}]+)\}", body):
        shared.setdefault("figures_used", [])
        if fname not in shared["figures_used"]:
            shared["figures_used"].append(fname)

    shared["history"].append({
        "step": len(shared["history"]) + 1,
        "stage": "writing", "label": section,
        "summary": body[:200].replace("\n", " "),
        "cost": usage["cost"], "error": None,
    })
    (Path(shared["output_path"]) / "history.json").write_text(
        json.dumps(shared["history"], indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[WritingLoop] '{section}' written ({len(body)} chars, ${usage['cost']:.4f})")


async def _writing_review_pass(shared: dict) -> tuple[str, list[dict]]:
    """Single writing quality gate — returns (action, major_comments)."""
    if not _budget_ok(shared, REVIEW_RESERVE_RATIO):
        return "compile", []

    await _assemble_tex(shared)

    tex = shared.get("tex_content", "")
    text, usage = await call_llm_async(
        f"""You are a rigorous peer reviewer. Evaluate this draft.

## Topic: {shared["topic"]}
## Draft (LaTeX)
{tex[:6000]}

## Artifacts
{_artifact_index(shared)}

## Available skills
{format_skill_index(shared.get("skill_index", {}))}

Flag only MAJOR issues (checklist violations, missing substance).
Return YAML:
```yaml
action: compile   # or: revise
comments:
  - id: 1
    severity: major
    section: <section>
    issue: <specific problem>
    fix: rewrite   # or: research
    skill: <skill>  # only when fix==research
```""",
        system=_REVIEW_SYSTEM,
        budget_remaining=shared.get("budget_remaining", 0),
        cost_log=shared.get("cost_log"),
    )
    track_cost(shared, "writing:review", usage)
    parsed = parse_yaml_response(text)
    if not isinstance(parsed, dict):
        return "compile", []
    action = parsed.get("action", "compile")
    major = [c for c in parsed.get("comments", []) if isinstance(c, dict) and c.get("severity") == "major"]
    return action, major


async def _assemble_tex(shared: dict):
    await _generate_workflow_diagram_async(shared)
    order = _section_order(shared)
    cleaned_bodies = {s: _sanitize_section_body(b) for s, b in shared.get("section_bodies", {}).items()}

    # Inject workflow diagram into Introduction
    out_dir = Path(shared["output_path"])
    workflow_fig = out_dir / "figures" / "workflow.png"
    if workflow_fig.exists() and "introduction" in cleaned_bodies:
        workflow_latex = (
            "\n\n\\begin{figure}[htbp]\n"
            "\\centering\n"
            "\\includegraphics[width=\\textwidth]{figures/workflow.png}\n"
            "\\caption{Overview of the study workflow.}\n"
            "\\label{fig:workflow}\n"
            "\\end{figure}\n\n"
        )
        intro = cleaned_bodies["introduction"]
        intro_lines = intro.split("\n")
        insert_at = 0
        for idx, ln in enumerate(intro_lines):
            if re.match(r"\\section\{", ln, re.IGNORECASE):
                insert_at = idx + 1
                break
        intro_lines.insert(insert_at, workflow_latex)
        cleaned_bodies["introduction"] = "\n".join(intro_lines)

    body = "\n\n".join(cleaned_bodies.get(s, "")
                       for s in order if s in cleaned_bodies and s != "abstract")
    abstract = re.sub(r"\\section\{[Aa]bstract\}\s*", "",
                      cleaned_bodies.get("abstract", "Abstract not available.")).strip()

    if shared.get("paper_title"):
        title = shared["paper_title"]
    else:
        title_text, title_usage = await call_llm_async(
            f"Generate a concise academic paper title (under {TITLE_MAX_WORDS} words) "
            f"based on this draft. Return ONLY the title.\n\n{body[:TITLE_TOPIC_CHARS]}",
            budget_remaining=shared.get("budget_remaining", 0),
            cost_log=shared.get("cost_log"),
        )
        track_cost(shared, "title:generate", title_usage)
        title = title_text.strip().strip('"').strip("'")
        shared["paper_title"] = title

    tex = (LATEX_SKELETON
           .replace("%% TITLE %%", title)
           .replace("%% ABSTRACT %%", abstract)
           .replace("%% BODY %%", body))
    bib = dedup_bibtex(shared.get("bibtex_entries", []))
    (out_dir / "report.tex").write_text(tex, encoding="utf-8")
    (out_dir / "references.bib").write_text(bib, encoding="utf-8")
    shared["tex_content"] = tex
    shared["bib_content"] = bib
    print(f"[assemble_tex] report.tex assembled ({len(tex)} chars)")


# ===========================================================================
# 1. Initializer
# ===========================================================================
class Initializer(Node):
    def prep(self, shared):
        return None

    def exec(self, prep_res):
        return None

    def post(self, shared, prep_res, exec_res):
        task_id = str(uuid.uuid4())
        out_dir = Path(shared.get("output_dir", "outputs")) / task_id
        out_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("artifacts", "figures", "data", "scripts"):
            (out_dir / sub).mkdir(exist_ok=True)
        shared.update({
            "output_path": str(out_dir),
            "budget_remaining": shared["budget_dollars"],
            "artifacts": {}, "bibtex_entries": [], "history": [],
            "sections_written": [], "section_bodies": {},
            "fix_attempts": 0, "cost_log": [],
        })
        print(f"[Initializer] {out_dir} | ${shared['budget_dollars']:.2f}")
        return "literature"


# ===========================================================================
# 2. LiteratureReviewLoop
# ===========================================================================
class LiteratureReviewLoop(AsyncNode):
    async def prep_async(self, shared):
        return shared

    async def exec_async(self, shared):
        return shared

    async def post_async(self, shared, prep_res, exec_res):
        literature_skills = {"paper-navigator", "research-survey", "research-ideation",
                             "evo-memory", "paper-planning"}
        skill_filter = lambda k: k in literature_skills

        stage_goal = (
            f"Build a thorough literature foundation for: {shared['topic']}. "
            "Find key papers, identify research gaps, and collect structured notes "
            "and BibTeX citations covering the state of the art."
        )
        reason = await _run_loop(shared, "literature", stage_goal, skill_filter)
        print(f"[LiteratureReviewLoop] exited: {reason}")
        return "experiment"


# ===========================================================================
# 3. ExperimentationLoop
# ===========================================================================
class ExperimentationLoop(AsyncNode):
    async def prep_async(self, shared):
        return shared

    async def exec_async(self, shared):
        return shared

    async def post_async(self, shared, prep_res, exec_res):
        experiment_skills = {"experiment-pipeline", "experiment-craft",
                             "experiment-iterative-coder", "evo-memory"}
        skill_filter = lambda k: k in experiment_skills

        stage_goal = (
            f"Design and execute experiments for: {shared['topic']}. "
            "Implement methods, collect data, generate figures, and produce "
            "quantitative results that support the paper's claims."
        )
        reason = await _run_loop(shared, "experiment", stage_goal, skill_filter)
        print(f"[ExperimentationLoop] exited: {reason}")
        return "write"


# ===========================================================================
# 4. WritingLoop
# ===========================================================================
class WritingLoop(AsyncNode):
    async def prep_async(self, shared):
        return shared

    async def exec_async(self, shared):
        return shared

    async def post_async(self, shared, prep_res, exec_res):
        sections = _section_order(shared)
        max_review_rounds = int(os.environ.get("MAX_REVIEW_ROUNDS", "1"))

        for round_i in range(max_review_rounds + 1):
            # Write any missing sections
            for section in sections:
                if not _budget_ok(shared, WRITE_RESERVE_RATIO):
                    print(f"[WritingLoop] Budget too low — stopping at section '{section}'.")
                    break
                if len(shared.get("section_bodies", {}).get(section, "").strip()) >= MIN_SECTION_LENGTH:
                    continue
                print(f"[WritingLoop] Writing section: {section}")
                await _write_section(section, shared)

            # Quality gate / review
            action, major_comments = await _writing_review_pass(shared)
            print(f"[WritingLoop] review round={round_i}: action={action}, major={len(major_comments)}")

            if action == "compile" or not major_comments:
                break
            if round_i >= max_review_rounds:
                break
            if not _budget_ok(shared, WRITE_RESERVE_RATIO):
                break

            # Address major comments
            valid_skills = set(shared.get("skill_index", {}).keys())
            for comment in major_comments:
                if not _budget_ok(shared, WRITE_RESERVE_RATIO):
                    break
                fix = comment.get("fix", "rewrite")
                section = comment.get("section", "")
                skill = comment.get("skill", "")
                print(f"[WritingLoop] Addressing: [{section}] {comment.get('issue','')[:80]}")
                if fix == "research" and skill and skill in valid_skills:
                    await _execute_skill(skill, "writing_revision", shared)
                if section:
                    # Force rewrite of the flagged section
                    shared.get("section_bodies", {}).pop(section, None)
                    await _write_section(section, shared)

        # Final tex assembly with title
        await _assemble_tex(shared)
        if not shared.get("paper_title"):
            order = _section_order(shared)
            cleaned = {s: _sanitize_section_body(b) for s, b in shared.get("section_bodies", {}).items()}
            draft_body = "\n\n".join(cleaned.get(s, "") for s in order if s in cleaned and s != "abstract")
            title_text, title_usage = await call_llm_async(
                f"Generate a concise academic paper title (under {TITLE_MAX_WORDS} words). "
                f"Return ONLY the title.\n\n{(draft_body or shared.get('topic',''))[:TITLE_TOPIC_CHARS]}",
                budget_remaining=shared.get("budget_remaining", 0),
                cost_log=shared.get("cost_log"),
            )
            track_cost(shared, "title:generate", title_usage)
            shared["paper_title"] = title_text.strip().strip('"').strip("'")
            print(f"[WritingLoop] Title: {shared['paper_title']}")

        return "compile"


# ===========================================================================
# 5. CompileTeX
# ===========================================================================
class CompileTeX(Node):
    def prep(self, shared):
        return shared["output_path"]

    def exec(self, out_dir):
        import shutil
        if not shutil.which("pdflatex"):
            return None, "pdflatex not found"
        all_output = []
        for cmd in [["pdflatex", "-interaction=nonstopmode", "report.tex"],
                    ["bibtex", "report"],
                    ["pdflatex", "-interaction=nonstopmode", "report.tex"],
                    ["pdflatex", "-interaction=nonstopmode", "report.tex"]]:
            r = subprocess.run(cmd, cwd=out_dir, capture_output=True,
                               text=True, errors="replace", timeout=LATEX_COMPILE_TIMEOUT)
            all_output.append(r.stdout + r.stderr)
        success = (Path(out_dir) / "report.pdf").exists()
        return success, "\n".join(all_output)

    def post(self, shared, prep_res, exec_res):
        success, log = exec_res
        if success is None:
            print(f"[CompileTeX] pdflatex not installed.")
            print(f"[CompileTeX] Source: {shared['output_path']}/report.tex")
            print(f"[CompileTeX] Run: cd {shared['output_path']} && "
                  "pdflatex report.tex && bibtex report && pdflatex report.tex && pdflatex report.tex")
            return "done"

        undefined = sorted(set(re.findall(r"Citation `([^']+)' on page", log)))
        if undefined:
            print(f"[CompileTeX] {len(undefined)} undefined citations: {', '.join(undefined[:10])}")
            shared["has_citation_warnings"] = True

        if success:
            if undefined and shared.get("fix_attempts", 0) < 2:
                shared["compile_errors"] = log
                shared["undefined_citations"] = undefined
                return "fix"
            print(f"[CompileTeX] PDF: {shared['output_path']}/report.pdf")
            return "done"

        shared["compile_errors"] = log
        print("[CompileTeX] Compilation failed → fix")
        return "fix"


# ===========================================================================
# 6. FixTeX
# ===========================================================================
class FixTeX(AsyncNode):
    async def prep_async(self, shared):
        undefined = shared.get("undefined_citations", [])
        return {
            "tex_content":        shared["tex_content"],
            "bib_content":        shared.get("bib_content", ""),
            "errors":             shared.get("compile_errors", ""),
            "attempt":            shared.get("fix_attempts", 0),
            "undefined_citations": undefined,
            "mode":               "citation" if undefined else "latex_error",
            "budget_remaining":   shared.get("budget_remaining", 0),
        }

    async def exec_async(self, prep_res):
        if prep_res["attempt"] >= 2:
            return None, {"input_tokens": 0, "output_tokens": 0, "cost": 0}
        if prep_res["mode"] == "citation":
            prompt = (
                f"Generate BibTeX entries for these undefined citation keys:\n"
                f"{', '.join(prep_res['undefined_citations'])}\n\n"
                f"Current .bib (do NOT repeat existing entries):\n{prep_res['bib_content'][:3000]}\n\n"
                "Return ONLY new BibTeX entries. Each MUST have: author, title, year, journal/booktitle."
            )
        else:
            errors = "\n".join(l for l in prep_res["errors"].split("\n")
                               if l.startswith("!") or "Error" in l or "Undefined" in l)[:1000]
            prompt = (
                f"Fix these LaTeX compilation errors. Return the COMPLETE corrected .tex file.\n\n"
                f"Errors:\n{errors}\n\nCurrent .tex:\n{prep_res['tex_content']}\n\n"
                "Rules: do not change \\documentclass or \\usepackage lines."
            )
        return await call_llm_async(prompt, budget_remaining=prep_res["budget_remaining"])

    async def post_async(self, shared, prep_res, exec_res):
        text, usage = exec_res
        shared["fix_attempts"] = prep_res["attempt"] + 1
        if text is None:
            print("[FixTeX] Max attempts reached.")
            return "done"
        track_cost(shared, f"fix_tex:{shared['fix_attempts']}", usage)
        cleaned = re.sub(r"^```\w*\n?", "", text.strip())
        cleaned = re.sub(r"\n?```$", "", cleaned)
        out_dir = Path(shared["output_path"])
        if prep_res["mode"] == "citation":
            _, new_entries = extract_bibtex(cleaned)
            if new_entries:
                combined = dedup_bibtex(shared.get("bibtex_entries", []) + new_entries)
                (out_dir / "references.bib").write_text(combined, encoding="utf-8")
                shared.update({"bib_content": combined,
                               "bibtex_entries": shared.get("bibtex_entries", []) + new_entries})
                print(f"[FixTeX] Added {len(new_entries)} BibTeX entries")
            shared.pop("undefined_citations", None)
        else:
            (out_dir / "report.tex").write_text(cleaned, encoding="utf-8")
            shared["tex_content"] = cleaned
            print(f"[FixTeX] Applied fix (attempt {shared['fix_attempts']})")
        return "compile"


# ===========================================================================
# 7. Finisher
# ===========================================================================
class Finisher(Node):
    def post(self, shared, prep_res, exec_res):
        from datetime import datetime, timezone
        total = sum(e["cost"] for e in shared.get("cost_log", []))
        out_dir = Path(shared["output_path"])
        (out_dir / "cost_log.json").write_text(
            json.dumps(shared.get("cost_log", []), indent=2, ensure_ascii=False), encoding="utf-8")
        (out_dir / "summary.json").write_text(
            json.dumps({
                "topic":            shared.get("topic", ""),
                "budget_dollars":   shared.get("budget_dollars", 0),
                "total_cost":       round(total, 6),
                "budget_remaining": round(shared.get("budget_remaining", 0), 6),
                "steps_executed":   len(shared.get("history", [])),
                "artifacts":        list(shared.get("artifacts", {}).keys()),
                "bibtex_count":     len(shared.get("bibtex_entries", [])),
                "fix_attempts":     shared.get("fix_attempts", 0),
                "completed_at":     datetime.now(timezone.utc).isoformat(),
            }, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n{'='*50}")
        print(f"Done! Total: ${total:.4f} / ${shared['budget_dollars']:.2f}")
        print(f"Output: {shared['output_path']}/")
        print(f"{'='*50}")
