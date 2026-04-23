"""PocketFlow nodes for the Autonomous Scientist agent.

Full pipeline:
  Initializer → PlanInitialExecutor → PlanDrivenExecutor (loop)
              → ReviewExecutor → [execute / compile]
              → CompileTeX ↔ FixTeX → Finisher

PlanInitialExecutor drafts a typed todo list (research + write steps).
PlanDrivenExecutor executes each step in order, optionally revising the plan.
ReviewExecutor appends new steps to the plan tail for revision.
LaTeX compilation happens exactly once, as the final PDF generation step.
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
    call_llm,
    call_llm_async,
    call_llm_with_tools,
    call_llm_with_tools_async,
    load_skill_content,
    load_mcp_config,
    filter_mcp_servers,
    format_mcp_context,
    format_skill_index,
    parse_yaml_response,
    extract_bibtex,
    dedup_bibtex,
    track_cost,
    estimate_calls_remaining,
)

# ---------------------------------------------------------------------------
# Configuration — all defaults live in .env; these are the fallback values
# used only when an env var is not set. Edit .env to change behaviour.
# ---------------------------------------------------------------------------
_DEFAULTS = {
    # Budget phase gates
    "BUDGET_RESERVE":          "0.03",
    "WRITE_RESERVE":           "0.015",
    "REVIEW_RESERVE":          "0.008",
    # Report-type budget thresholds
    "BUDGET_QUICK_SUMMARY":    "0.10",
    "BUDGET_LITERATURE_REVIEW":"0.50",
    "BUDGET_RESEARCH_REPORT":  "2.00",
    # Execution timeouts (seconds)
    "CODE_EXEC_TIMEOUT":       "300",
    "LATEX_COMPILE_TIMEOUT":   "60",
    # Plan revision cadence (every N completed research steps)
    "PLAN_REVISE_EVERY":       "3",
    # Prompt context window sizes (chars)
    "SKILL_CONTENT_LIMIT":     "1500",
    "ARTIFACT_CONTEXT_CHARS":  "2500",
    "PRIOR_SECTION_CHARS":     "800",
    "SALVAGE_CONTEXT_CHARS":   "3000",
    "TITLE_TOPIC_CHARS":       "2000",
    # Quality gates
    "MIN_SECTION_LENGTH":      "100",
    "TITLE_MAX_WORDS":         "15",
    # Step decomposition
    "STEP_INSTRUCTION_MAX_WORDS": "30",
}

def _cfg(key: str, cast=float) -> float:
    return cast(os.environ.get(key, _DEFAULTS[key]))

BUDGET_RESERVE          = _cfg("BUDGET_RESERVE")
WRITE_RESERVE           = _cfg("WRITE_RESERVE")
REVIEW_RESERVE          = _cfg("REVIEW_RESERVE")
BUDGET_QUICK_SUMMARY    = _cfg("BUDGET_QUICK_SUMMARY")
BUDGET_LITERATURE_REVIEW= _cfg("BUDGET_LITERATURE_REVIEW")
BUDGET_RESEARCH_REPORT  = _cfg("BUDGET_RESEARCH_REPORT")
CODE_EXEC_TIMEOUT       = _cfg("CODE_EXEC_TIMEOUT",     int)
LATEX_COMPILE_TIMEOUT   = _cfg("LATEX_COMPILE_TIMEOUT", int)
PLAN_REVISE_EVERY       = _cfg("PLAN_REVISE_EVERY",     int)
SKILL_CONTENT_LIMIT     = _cfg("SKILL_CONTENT_LIMIT",   int)
ARTIFACT_CONTEXT_CHARS  = _cfg("ARTIFACT_CONTEXT_CHARS",int)
PRIOR_SECTION_CHARS     = _cfg("PRIOR_SECTION_CHARS",   int)
SALVAGE_CONTEXT_CHARS   = _cfg("SALVAGE_CONTEXT_CHARS", int)
TITLE_TOPIC_CHARS       = _cfg("TITLE_TOPIC_CHARS",     int)
MIN_SECTION_LENGTH           = _cfg("MIN_SECTION_LENGTH",           int)
TITLE_MAX_WORDS              = _cfg("TITLE_MAX_WORDS",              int)
STEP_INSTRUCTION_MAX_WORDS   = _cfg("STEP_INSTRUCTION_MAX_WORDS",   int)

SECTION_ORDER = {
    "Quick Summary":     ["abstract", "introduction", "discussion", "conclusion"],
    "Literature Review": ["abstract", "introduction", "background", "discussion", "conclusion"],
    "Research Report":   ["abstract", "introduction", "background", "methods", "results", "discussion", "conclusion"],
    "Full Paper":        ["abstract", "introduction", "background", "methods", "results", "discussion", "conclusion", "limitations"],
}

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
# Module-level helpers (shared across nodes)
# ---------------------------------------------------------------------------

def _report_type(budget: float) -> str:
    if budget < BUDGET_QUICK_SUMMARY:     return "Quick Summary"
    if budget < BUDGET_LITERATURE_REVIEW: return "Literature Review"
    if budget < BUDGET_RESEARCH_REPORT:   return "Research Report"
    return "Full Paper"


def _section_order(shared: dict) -> list[str]:
    return SECTION_ORDER.get(shared.get("report_type", "Literature Review"),
                              SECTION_ORDER["Literature Review"])


def _required_sections(shared: dict) -> list[str]:
    has_methods = any(k in shared.get("artifacts", {})
                      for k in ("statistical-analysis", "method-implementation",
                                "experimental-evaluation"))
    return [s for s in _section_order(shared) if s not in ("methods", "results") or has_methods]


def _artifact_index(shared: dict) -> str:
    """filepath + first line for every artifact .md file."""
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


def _recent_history(shared: dict, n: int) -> str:
    history = shared.get("history", [])
    return "\n".join(
        f"- {h['skill']}/{h.get('step_label','')}: {h['summary'][:120]}"
        + (f" [ERR: {h['error']}]" if h.get("error") else "")
        for h in history[-n:]
    ) or "No history yet."


def _plan_context(shared: dict) -> str:
    """Return a lookahead/lookback view of the plan, correctly bounded at borders.

    Near the start: almost no completed steps, many remaining steps shown.
    Near the end: few remaining steps (1-2), full history of completed steps.

    The lookback window mirrors LOOKBACK env var; lookahead window mirrors it too
    but is naturally capped by how many plan items remain.
    """
    plan = shared.get("plan", [])
    if not plan:
        return ""

    lookback_n = int(os.environ.get("LOOKBACK", "3"))

    done   = [t for t in plan if t.get("status") == "done"]
    failed = [t for t in plan if t.get("status") == "failed"]
    active = [t for t in plan if t.get("status") == "in_progress"]
    pending = [t for t in plan if t.get("status") == "pending"]

    lines = [f"## Research Plan ({len(done)}/{len(plan)} steps complete, {len(failed)} failed)"]

    # Lookback: last min(lookback_n, len(done)) completed steps
    shown_done = done[-lookback_n:] if done else []
    if shown_done:
        lines.append("### Completed (recent)")
        for t in shown_done:
            lines.append(f"  [x] {t['id']}. {t['task']}")
        if len(done) > lookback_n:
            lines.append(f"  ... ({len(done) - lookback_n} earlier steps)")

    # Failed steps — always show so writer knows data may be missing
    if failed:
        lines.append("### Failed (incomplete — tool rounds exhausted; data may be partial)")
        for t in failed:
            lines.append(f"  [!] {t['id']}. {t['task']}")

    # Active step
    for t in active:
        lines.append(f"  [>] {t['id']}. {t['task']}  ← current")

    # Lookahead: remaining steps (all of them — naturally shrinks to 1-2 near end)
    if pending:
        lines.append("### Upcoming")
        for t in pending:
            lines.append(f"  [ ] {t['id']}. {t['task']}")

    return "\n".join(lines)


def _extend_bibtex(shared: dict, new_entries: list[str]):
    """Add BibTeX entries to shared store, skipping keys already present."""
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
    """Strip all non-body LaTeX leakage from an LLM-generated section."""
    body = re.sub(r"%%BEGIN BIBTEX%%.*?%%END BIBTEX%%", "", body, flags=re.DOTALL)
    body = re.sub(r"\\begin\{thebibliography\}.*?\\end\{thebibliography\}", "", body, flags=re.DOTALL)
    body = re.sub(r"\\bibliographystyle\{[^}]*\}", "", body)
    body = re.sub(r"\\bibliography\{[^}]*\}", "", body)
    # Strip any accidentally included preamble (\documentclass, \usepackage, \begin{document})
    body = re.sub(r"\\documentclass\b.*?\n", "", body)
    body = re.sub(r"\\usepackage\b.*?\n", "", body)
    body = re.sub(r"\\begin\{document\}|\\end\{document\}", "", body)
    body = re.sub(r"\\maketitle\b", "", body)
    return body.strip()


def _existing_files(shared: dict) -> str:
    """List all files already written to data/, figures/, scripts/ for scaffolding context."""
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
    """Scan output data/ dir for JSON/CSV files and extract key numeric statistics.

    Returns a compact text block injected into writing prompts so the LLM uses
    real numbers instead of hallucinating them.
    """
    out_dir = Path(shared.get("output_path", ""))
    data_dir = out_dir / "data"
    if not data_dir.is_dir():
        return ""

    lines = ["## Data files (USE THESE EXACT NUMBERS in your section — do not invent statistics)"]
    found_any = False

    for f in sorted(data_dir.iterdir()):
        if not f.is_file():
            continue
        if f.suffix.lower() == ".json":
            try:
                raw = f.read_text(encoding="utf-8", errors="replace")
                data = json.loads(raw)
                # Flatten top-level scalar fields as key=value pairs
                stats = []
                def _flatten(obj, prefix=""):
                    if isinstance(obj, dict):
                        for k, v in list(obj.items())[:30]:
                            _flatten(v, f"{prefix}{k}.")
                    elif isinstance(obj, list):
                        stats.append(f"{prefix}count={len(obj)}")
                        # Sample first element keys
                        if obj and isinstance(obj[0], dict):
                            stats.append(f"{prefix}fields={list(obj[0].keys())[:8]}")
                    elif isinstance(obj, (int, float, str, bool)) and obj is not None:
                        val = str(obj)[:120]
                        stats.append(f"{prefix[:-1]}={val}")
                _flatten(data)
                if stats:
                    lines.append(f"### {f.name}")
                    lines.extend(f"  {s}" for s in stats[:40])
                    found_any = True
            except Exception:
                pass
        elif f.suffix.lower() == ".csv":
            try:
                raw = f.read_text(encoding="utf-8", errors="replace")
                rows = [r for r in raw.splitlines() if r.strip()]
                if rows:
                    lines.append(f"### {f.name}")
                    lines.append(f"  rows={len(rows) - 1}  columns={rows[0][:200]}")
                    found_any = True
            except Exception:
                pass

    return "\n".join(lines) if found_any else ""


def _run_code_blocks(text: str, skill_name: str, task_dir: Path, shared: dict) -> list[str]:
    """Execute %%BEGIN CODE:lang%% ... %%END CODE%% blocks."""
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
        script = task_dir / "scripts" / f"{step_num:02d}_{skill_name}_{i:02d}{ext}"
        script.write_text(code, encoding="utf-8")
        cmd = ["python", str(script)] if lang == "python" else ["bash", str(script)]
        try:
            r = subprocess.run(cmd, cwd=str(task_dir), capture_output=True,
                               text=True, errors="replace", timeout=CODE_EXEC_TIMEOUT,
                               env={**os.environ})
            outputs.append(f"[{script.name}] exit={r.returncode}\n{r.stdout[:3000]}")
            if r.returncode != 0:
                outputs.append(f"[STDERR] {r.stderr[:1000]}")
                shared.setdefault("failed_code", {})[skill_name] = {
                    "script": str(script), "error": r.stderr or f"exit {r.returncode}", "code": code}
            print(f"[code] {script.name}: exit={r.returncode}")
        except subprocess.TimeoutExpired:
            outputs.append(f"[{script.name}] TIMEOUT")
            shared.setdefault("failed_code", {})[skill_name] = {
                "script": str(script), "error": "TIMEOUT after 300s", "code": code}
        except Exception as e:
            outputs.append(f"[{script.name}] ERROR: {e}")
            shared.setdefault("failed_code", {})[skill_name] = {
                "script": str(script), "error": str(e), "code": code}
    return outputs


def _save_artifact(text: str, skill_name: str, step_label: str,
                   code_outputs: list[str], usage: dict, shared: dict):
    """Extract BibTeX, strip code blocks, persist artifact + history entry."""
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

    shared["artifacts"][skill_name] = (shared["artifacts"].get(skill_name, "") + "\n\n" + main).strip()
    _extend_bibtex(shared, entries)

    out_dir = Path(shared["output_path"])
    (out_dir / "artifacts").mkdir(exist_ok=True)
    step_num = len(shared["history"]) + 1
    (out_dir / "artifacts" / f"{step_num:02d}_{skill_name}_{step_label}.md").write_text(main, encoding="utf-8")
    if entries:
        (out_dir / "artifacts" / f"{step_num:02d}_{skill_name}_{step_label}.bib").write_text(
            "\n\n".join(entries) + "\n", encoding="utf-8")

    summary = (main[:200] + "...").replace("\n", " ") if len(main) > 200 else main[:200].replace("\n", " ")
    shared["history"].append({
        "step": step_num, "skill": skill_name, "step_label": step_label,
        "summary": summary, "cost": usage["cost"],
        "error": shared.get("failed_code", {}).get(skill_name, {}).get("error"),
    })
    (out_dir / "history.json").write_text(
        json.dumps(shared["history"], indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[artifact] {skill_name}/{step_label}: {len(entries)} bibtex, ${usage['cost']:.4f}")


def _run_skill(skill_name: str, shared: dict):
    """Decompose a skill and execute all its steps. Modifies shared in-place."""
    budget = shared.get("budget_remaining", 0)
    skill_content, meta = load_skill_content(shared["skills_dir"], skill_name)
    can_execute = "Bash" in meta.get("allowed-tools", [])
    out_dir = Path(shared["output_path"])

    # Load MCP context once per skill (injected into step prompts when Bash is available)
    mcp_context = ""
    if can_execute:
        api_keys = shared.get("api_keys", {})
        raw_servers = load_mcp_config()
        available_servers = filter_mcp_servers(raw_servers, api_keys)
        mcp_context = format_mcp_context(available_servers)

    # Decompose
    text, usage = call_llm(
        f"""Decompose this research skill into 2-5 atomic steps.

## Topic: {shared["topic"]}
## Skill: {skill_name}
## Instructions:
{skill_content[:SKILL_CONTENT_LIMIT]}

Return YAML list only:
```yaml
- step: 1
  instruction: <one focused action, under {STEP_INSTRUCTION_MAX_WORDS} words>
  needs_code: <true/false>
```""",
        budget_remaining=budget)
    track_cost(shared, f"research:decompose:{skill_name}", usage)
    parsed = parse_yaml_response(text)
    steps = parsed if isinstance(parsed, list) else [
        {"step": 1, "instruction": "Execute the full skill.", "needs_code": can_execute}]

    scripts_dir = Path(shared["skills_dir"]) / skill_name / "scripts"
    available_scripts = ([f.name for f in scripts_dir.iterdir() if f.suffix == ".py"]
                         if scripts_dir.is_dir() else [])
    data_files = [str(f.relative_to(out_dir))
                  for sub in ("data", "figures")
                  for f in (out_dir / sub).iterdir() if f.is_file()] if out_dir.exists() else []

    for s in steps:
        if shared.get("budget_remaining", 0) < BUDGET_RESERVE:
            print(f"[ResearchExecutor] Budget exhausted mid-skill, stopping.")
            break
        instruction = s.get("instruction", "Execute the full skill.")
        needs_code = bool(s.get("needs_code")) and can_execute

        abs_out = str(Path(shared["output_path"]).resolve())
        mcp_block = f"\n\n{mcp_context}" if mcp_context else ""
        existing_files = _existing_files(shared)
        step_prompt = f"""Execute this single research step. Be focused and concise.

## Topic: {shared["topic"]}
## Step: {instruction}
## Recent context: {_recent_history(shared, 5)}
## Working directory (ABSOLUTE): {abs_out}
## Already produced files (DO NOT recreate these — read them directly if needed):
{existing_files}
## Environment: All API keys (OPENROUTER_API_KEY, PERPLEXITY_API_KEY, GITHUB_TOKEN, HF_TOKEN, OPENAI_API_KEY, etc.) are pre-loaded from .env and available as environment variables — use them directly in bash commands without reading .env yourself.{mcp_block}

CRITICAL working-directory rules:
- ALL bash commands run inside {abs_out} — this is enforced by the shell.
- Save data files to `{abs_out}/data/` — use ABSOLUTE paths in every command.
- Save figures to `{abs_out}/figures/` — use ABSOLUTE paths in every command.
- NEVER use bare `cd` to change to a different base directory. Use absolute paths instead.
- No plt.show(). Use plt.savefig("{abs_out}/figures/<name>.png") explicitly.
- For `pip install` or package setup, use `pip install -q --exists-action i <pkg>` and redirect stdout to avoid wasting tool rounds.
- Do NOT re-fetch or re-compute data that already exists in the files listed above.
- NEVER use `sleep` — it wastes tool rounds and triggers timeouts. Use immediate retries instead.
- NEVER use `npx`, `npm`, or `node` — MCP servers are pre-configured and available as environment variables, not via npx.
- Write large files with Python (`open(...).write(...)`) not shell heredocs (`cat > file << 'EOF'`) — heredocs time out on large content.

After completing the step, summarise findings and append any citations:

%%BEGIN BIBTEX%%
@article{{key, author={{...}}, title={{...}}, journal={{...}}, year={{YYYY}}}}
%%END BIBTEX%%

CITATION RULES:
- Keys MUST follow author+year format (e.g. smith2023attention, vaswani2017transformer).
- Do NOT use internal filenames as keys (e.g. dataset_metadata, classification_results, analysis_report).
- Every entry MUST have author= and title= fields with real values."""

        if needs_code:
            # Use tool-calling loop: model drives bash execution with error feedback.
            step_text, step_usage = call_llm_with_tools(
                step_prompt,
                budget_remaining=shared.get("budget_remaining", 0),
                cwd=abs_out,
            )
            code_outputs = []  # execution happened inside the tool loop
            if step_usage.get("tool_rounds_exhausted"):
                shared.setdefault("exhausted_steps", []).append(
                    {"skill": skill_name, "step": s.get("step", 1)})
                print(f"[ResearchExecutor] WARNING: {skill_name}/step{s.get('step',1)} "
                      f"hit max tool rounds — issuing summary call to salvage partial findings.")
                # Scope-narrowing salvage: ask the model to summarise what it found so far
                salvage_text, salvage_usage = call_llm(
                    f"""You were executing a research step but ran out of tool rounds.
Summarise ALL findings and data you collected so far in this step.
Include any file paths created, statistics found, and key results.
This summary will be used by the paper-writing agent.

## Step that was executing:
{instruction}

## Partial output so far:
{step_text[:SALVAGE_CONTEXT_CHARS]}

Write a concise summary (200-400 words) of findings, then cite any relevant papers:
%%BEGIN BIBTEX%%
@article{{key, author={{...}}, title={{...}}, year={{YYYY}}}}
%%END BIBTEX%%""",
                    budget_remaining=shared.get("budget_remaining", 0),
                )
                track_cost(shared, f"research:salvage:{skill_name}", salvage_usage)
                # Append salvage summary to step text so artifact captures it
                step_text = step_text + "\n\n## SALVAGE SUMMARY\n" + salvage_text
        else:
            step_text, step_usage = call_llm(
                step_prompt,
                budget_remaining=shared.get("budget_remaining", 0),
            )
            code_outputs = []

        track_cost(shared, f"research:step:{skill_name}", step_usage)
        _save_artifact(step_text, skill_name, f"step{s.get('step',1)}",
                       code_outputs, step_usage, shared)
        print(f"[ResearchExecutor] {skill_name}/step{s.get('step',1)} done. "
              f"Budget: ${shared['budget_remaining']:.4f}")


def _write_section(section: str, shared: dict):
    """Write one LaTeX section and persist it. Modifies shared in-place."""
    out_dir = Path(shared["output_path"])
    cite_keys = [m.group(1).strip()
                 for e in shared.get("bibtex_entries", [])
                 for m in [re.match(r"@\w+\{([^,]+),", e)] if m]
    artifact_text = "\n\n".join(f"### {k}\n{v[:ARTIFACT_CONTEXT_CHARS]}"
                                for k, v in shared.get("artifacts", {}).items())
    prior_text = ("\n\n## Prior sections\n" +
                  "\n\n".join(f"### {s}\n{b[:PRIOR_SECTION_CHARS]}"
                              for s, b in shared.get("section_bodies", {}).items())
                  if shared.get("section_bodies") else "")
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
            lines.append("### Already used in previous sections (avoid reusing unless essential):")
            lines.extend(f"- {f}" for f in used_figures)
        lines.append("\nUse this LaTeX pattern for each figure you include:")
        lines.append(r"""```latex
\begin{figure}[htbp]
\centering
\includegraphics[width=0.8\textwidth]{figures/<filename>}
\caption{<descriptive caption>}
\label{fig:<label>}
\end{figure}
```""")
        figures_block = "\n".join(lines)
    data_block = _data_summary(shared)
    if data_block:
        data_block = "\n\n" + data_block

    text, usage = call_llm(
        f"""Write the **{section}** section of a {shared.get("report_type","Literature Review").lower()} as compilable LaTeX.

## Topic: {shared["topic"]}
## Artifacts
{artifact_text}{prior_text}{figures_block}{data_block}

## BibTeX keys: {", ".join(cite_keys) or "No citations yet."}

## Rules
- Output ONLY this section's LaTeX (\\section{{...}} onward). No preamble, no \\documentclass, no \\usepackage.
- Use \\cite{{key}} only from keys above. Back every claim.
- Active voice, formal tone. Escape \\%, \\&, \\#, \\$.
- Abstract: 150-250 words, no \\cite.
- Do NOT include \\bibliography, \\bibliographystyle, or \\begin{{thebibliography}} — the skeleton handles these.
- Tables: use \\resizebox{{\\textwidth}}{{!}}{{...}} around wide tabular environments to prevent overflow. Keep all tables to a CONSISTENT column count and font size so reading experience is uniform across the paper.
- Long URLs: wrap with \\url{{...}} so they break across lines.
- Figures: use [htbp] placement and \\includegraphics[width=0.9\\textwidth]{{figures/<filename>}}.
- Workflow figure: if `workflow.png` appears in the **Available** figures list above (not the "Already used" list), include it ONCE in this section to show the study design. Do NOT include it if it is already listed as "Already used".
- Hyperparameter details: report only the FINAL chosen values and 1-sentence justification. Do NOT describe the full tuning search, grid details, or intermediate results.
- Visualizations: ALL charts and plots MUST be generated with seaborn or plotly. Never use single-color bar charts — use a distinct color per category/group.
- Section order: Conclusion MUST be the final section (unless an explicit Appendix follows). Never place any content section after Conclusion.

%%BEGIN SECTION%%
\\section{{{section.title()}}}
...
%%END SECTION%%

%%BEGIN BIBTEX%%
@article{{key, ...}}
%%END BIBTEX%%""",
        budget_remaining=shared.get("budget_remaining", 0))
    track_cost(shared, f"writing:{section}", usage)

    sec_m = re.search(r"%%BEGIN SECTION%%(.*?)%%END SECTION%%", text, re.DOTALL)
    body = _sanitize_section_body(sec_m.group(1) if sec_m else text)
    bib_m = re.search(r"%%BEGIN BIBTEX%%(.*?)%%END BIBTEX%%", text, re.DOTALL)
    if bib_m:
        new_entries = [e.strip() for e in re.findall(r"(@\w+\{[^@]+)", bib_m.group(1), re.DOTALL) if e.strip()]
        _extend_bibtex(shared, new_entries)

    # Retry once if body is suspiciously short (LLM missed markers or refused)
    if len(body.strip()) < MIN_SECTION_LENGTH:
        print(f"[PlanDrivenExecutor] '{section}' too short ({len(body)} chars) — retrying with strict prompt")
        retry_text, retry_usage = call_llm(
            f"""IMPORTANT: You MUST output the {section} section content between the markers below.
Previous attempt produced no usable content. Do not skip this.

## Topic: {shared["topic"]}
## Artifacts
{artifact_text[:ARTIFACT_CONTEXT_CHARS]}
## BibTeX keys: {", ".join(cite_keys) or "No citations yet."}

Rules:
- Output ONLY LaTeX starting with \\section{{{section.title()}}}
- Use \\cite{{key}} only from the keys listed above
- Do NOT include bibliography or preamble

%%BEGIN SECTION%%
\\section{{{section.title()}}}
...your content here...
%%END SECTION%%""",
            budget_remaining=shared.get("budget_remaining", 0))
        track_cost(shared, f"writing:{section}:retry", retry_usage)
        retry_sec_m = re.search(r"%%BEGIN SECTION%%(.*?)%%END SECTION%%", retry_text, re.DOTALL)
        retry_body = _sanitize_section_body(retry_sec_m.group(1) if retry_sec_m else retry_text)
        if len(retry_body.strip()) > len(body.strip()):
            body = retry_body
            print(f"[PlanDrivenExecutor] '{section}' retry succeeded ({len(body)} chars)")
        else:
            print(f"[PlanDrivenExecutor] '{section}' retry also short ({len(retry_body)} chars) — keeping best")

    shared.setdefault("section_bodies", {})[section] = body
    if section not in shared.get("sections_written", []):
        shared.setdefault("sections_written", []).append(section)
    # Track which figures this section referenced to avoid reuse in later sections
    for fname in re.findall(r"\\includegraphics[^{]*\{figures/([^}]+)\}", body):
        shared.setdefault("figures_used", [])
        if fname not in shared["figures_used"]:
            shared["figures_used"].append(fname)
    # Mark section as failed in plan if still too short after retry
    if len(body.strip()) < MIN_SECTION_LENGTH:
        print(f"[PlanDrivenExecutor] WARNING: '{section}' body still empty after retry")
    print(f"[PlanDrivenExecutor] '{section}' written ({len(body)} chars, ${usage['cost']:.4f})")


def _build_workflow_prompt(shared: dict) -> str:
    """Build a gpt-image-2 prompt describing the research workflow from the executed plan."""
    plan = shared.get("plan", [])
    topic = shared.get("topic", "research")[:80]

    research_steps = [t["task"][:50] for t in plan if t.get("type") == "research"]
    write_steps    = [t.get("section") or t["task"][:40]
                      for t in plan if t.get("type") == "write"]

    research_block = " → ".join(research_steps) if research_steps else "Literature survey"
    write_block    = " → ".join(write_steps)    if write_steps    else "Report writing"

    return (
        f"A clean, professional research workflow diagram for a scientific paper titled '{topic}'. "
        f"Show a left-to-right flowchart with two swim-lanes: "
        f"top lane labelled 'Research' contains boxes: {research_block}; "
        f"bottom lane labelled 'Writing' contains boxes: {write_block}. "
        "Arrows connect steps in each lane, and a vertical arrow links the Research lane to the Writing lane. "
        "White background, flat design, muted blue and green color scheme, "
        "sans-serif labels, no decorative elements."
    )


async def _generate_workflow_diagram_async(shared: dict):
    """Generate a study-workflow diagram via gpt-image-2, run in executor to avoid blocking.

    Skipped silently if the figure already exists or OPENROUTER_API_KEY is absent.
    Falls back to a no-op (no matplotlib dependency) so the report still compiles.
    """
    out_dir = Path(shared["output_path"])
    dest = out_dir / "figures" / "workflow.png"
    if dest.exists():
        return

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("[workflow] OPENROUTER_API_KEY not set — skipping workflow diagram")
        return

    # Locate the generate.py script relative to this file's project root
    project_root = Path(__file__).resolve().parents[1]
    generate_script = project_root / "skills" / "gpt-image-2" / "scripts" / "generate.py"
    if not generate_script.exists():
        print(f"[workflow] gpt-image-2 script not found at {generate_script} — skipping")
        return

    prompt = _build_workflow_prompt(shared)

    def _run():
        r = subprocess.run(
            ["python", str(generate_script),
             "-p", prompt,
             "-f", str(dest),
             "--size", "1536x1024",
             "--quality", "medium"],
            capture_output=True, text=True, errors="replace",
            timeout=120,
            env={**os.environ},
        )
        return r

    try:
        r = await asyncio.get_event_loop().run_in_executor(None, _run)
        if r.returncode == 0 and dest.exists():
            print(f"[workflow] diagram saved → {dest}")
        else:
            print(f"[workflow] gpt-image-2 failed (exit={r.returncode}): {r.stderr[:200]}")
    except Exception as e:
        print(f"[workflow] diagram generation error: {e}")


async def _run_skill_async(skill_name: str, shared: dict):
    """Async version of _run_skill — LLM calls are awaited concurrently where possible."""
    budget = shared.get("budget_remaining", 0)
    skill_content, meta = load_skill_content(shared["skills_dir"], skill_name)
    can_execute = "Bash" in meta.get("allowed-tools", [])
    out_dir = Path(shared["output_path"])

    mcp_context = ""
    if can_execute:
        api_keys = shared.get("api_keys", {})
        raw_servers = load_mcp_config()
        available_servers = filter_mcp_servers(raw_servers, api_keys)
        mcp_context = format_mcp_context(available_servers)

    text, usage = await call_llm_async(
        f"""Decompose this research skill into 2-5 atomic steps.

## Topic: {shared["topic"]}
## Skill: {skill_name}
## Instructions:
{skill_content[:SKILL_CONTENT_LIMIT]}

Return YAML list only:
```yaml
- step: 1
  instruction: <one focused action, under {STEP_INSTRUCTION_MAX_WORDS} words>
  needs_code: <true/false>
```""",
        budget_remaining=budget)
    track_cost(shared, f"research:decompose:{skill_name}", usage)
    parsed = parse_yaml_response(text)
    steps = parsed if isinstance(parsed, list) else [
        {"step": 1, "instruction": "Execute the full skill.", "needs_code": can_execute}]

    scripts_dir = Path(shared["skills_dir"]) / skill_name / "scripts"
    available_scripts = ([f.name for f in scripts_dir.iterdir() if f.suffix == ".py"]
                         if scripts_dir.is_dir() else [])
    data_files = [str(f.relative_to(out_dir))
                  for sub in ("data", "figures")
                  for f in (out_dir / sub).iterdir() if f.is_file()] if out_dir.exists() else []

    for s in steps:
        if shared.get("budget_remaining", 0) < BUDGET_RESERVE:
            print(f"[ResearchExecutor] Budget exhausted mid-skill, stopping.")
            break
        instruction = s.get("instruction", "Execute the full skill.")
        needs_code = bool(s.get("needs_code")) and can_execute

        abs_out = str(Path(shared["output_path"]).resolve())
        mcp_block = f"\n\n{mcp_context}" if mcp_context else ""
        existing_files = _existing_files(shared)
        step_prompt = f"""Execute this single research step. Be focused and concise.

## Topic: {shared["topic"]}
## Step: {instruction}
## Recent context: {_recent_history(shared, 5)}
## Working directory (ABSOLUTE): {abs_out}
## Already produced files (DO NOT recreate these — read them directly if needed):
{existing_files}
## Environment: All API keys (OPENROUTER_API_KEY, PERPLEXITY_API_KEY, GITHUB_TOKEN, HF_TOKEN, OPENAI_API_KEY, etc.) are pre-loaded from .env and available as environment variables — use them directly in bash commands without reading .env yourself.{mcp_block}

CRITICAL working-directory rules:
- ALL bash commands run inside {abs_out} — this is enforced by the shell.
- Save data files to `{abs_out}/data/` — use ABSOLUTE paths in every command.
- Save figures to `{abs_out}/figures/` — use ABSOLUTE paths in every command.
- NEVER use bare `cd` to change to a different base directory. Use absolute paths instead.
- No plt.show(). Use plt.savefig("{abs_out}/figures/<name>.png") explicitly.
- For `pip install` or package setup, use `pip install -q --exists-action i <pkg>` and redirect stdout to avoid wasting tool rounds.
- Do NOT re-fetch or re-compute data that already exists in the files listed above.
- NEVER use `sleep` — it wastes tool rounds and triggers timeouts. Use immediate retries instead.
- NEVER use `npx`, `npm`, or `node` — MCP servers are pre-configured and available as environment variables, not via npx.
- Write large files with Python (`open(...).write(...)`) not shell heredocs (`cat > file << 'EOF'`) — heredocs time out on large content.

After completing the step, summarise findings and append any citations:

%%BEGIN BIBTEX%%
@article{{key, author={{...}}, title={{...}}, journal={{...}}, year={{YYYY}}}}
%%END BIBTEX%%

CITATION RULES:
- Keys MUST follow author+year format (e.g. smith2023attention, vaswani2017transformer).
- Do NOT use internal filenames as keys (e.g. dataset_metadata, classification_results, analysis_report).
- Every entry MUST have author= and title= fields with real values."""

        if needs_code:
            step_text, step_usage = await call_llm_with_tools_async(
                step_prompt,
                budget_remaining=shared.get("budget_remaining", 0),
                cwd=abs_out,
            )
            code_outputs = []
            if step_usage.get("tool_rounds_exhausted"):
                shared.setdefault("exhausted_steps", []).append(
                    {"skill": skill_name, "step": s.get("step", 1)})
                print(f"[ResearchExecutor] WARNING: {skill_name}/step{s.get('step',1)} "
                      f"hit max tool rounds — issuing summary call to salvage partial findings.")
                salvage_text, salvage_usage = await call_llm_async(
                    f"""You were executing a research step but ran out of tool rounds.
Summarise ALL findings and data you collected so far in this step.
Include any file paths created, statistics found, and key results.
This summary will be used by the paper-writing agent.

## Step that was executing:
{instruction}

## Partial output so far:
{step_text[:SALVAGE_CONTEXT_CHARS]}

Write a concise summary (200-400 words) of findings, then cite any relevant papers:
%%BEGIN BIBTEX%%
@article{{key, author={{...}}, title={{...}}, year={{YYYY}}}}
%%END BIBTEX%%""",
                    budget_remaining=shared.get("budget_remaining", 0),
                )
                track_cost(shared, f"research:salvage:{skill_name}", salvage_usage)
                step_text = step_text + "\n\n## SALVAGE SUMMARY\n" + salvage_text
        else:
            step_text, step_usage = await call_llm_async(
                step_prompt,
                budget_remaining=shared.get("budget_remaining", 0),
            )
            code_outputs = []

        track_cost(shared, f"research:step:{skill_name}", step_usage)
        _save_artifact(step_text, skill_name, f"step{s.get('step',1)}",
                       code_outputs, step_usage, shared)
        print(f"[ResearchExecutor] {skill_name}/step{s.get('step',1)} done. "
              f"Budget: ${shared['budget_remaining']:.4f}")


async def _write_section_async(section: str, shared: dict):
    """Async version of _write_section."""
    out_dir = Path(shared["output_path"])
    cite_keys = [m.group(1).strip()
                 for e in shared.get("bibtex_entries", [])
                 for m in [re.match(r"@\w+\{([^,]+),", e)] if m]
    artifact_text = "\n\n".join(f"### {k}\n{v[:ARTIFACT_CONTEXT_CHARS]}"
                                for k, v in shared.get("artifacts", {}).items())
    prior_text = ("\n\n## Prior sections\n" +
                  "\n\n".join(f"### {s}\n{b[:PRIOR_SECTION_CHARS]}"
                              for s, b in shared.get("section_bodies", {}).items())
                  if shared.get("section_bodies") else "")
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
            lines.append("### Already used in previous sections (avoid reusing unless essential):")
            lines.extend(f"- {f}" for f in used_figures)
        lines.append("\nUse this LaTeX pattern for each figure you include:")
        lines.append(r"""```latex
\begin{figure}[htbp]
\centering
\includegraphics[width=0.9\textwidth]{figures/<filename>}
\caption{<descriptive caption>}
\label{fig:<label>}
\end{figure}
```""")
        figures_block = "\n".join(lines)
    data_block = _data_summary(shared)
    if data_block:
        data_block = "\n\n" + data_block

    text, usage = await call_llm_async(
        f"""Write the **{section}** section of a {shared.get("report_type","Literature Review").lower()} as compilable LaTeX.

## Topic: {shared["topic"]}
## Artifacts
{artifact_text}{prior_text}{figures_block}{data_block}

## BibTeX keys: {", ".join(cite_keys) or "No citations yet."}

## Rules
- Output ONLY this section's LaTeX (\\section{{...}} onward). No preamble, no \\documentclass, no \\usepackage.
- Use \\cite{{key}} only from keys above. Back every claim.
- Active voice, formal tone. Escape \\%, \\&, \\#, \\$.
- Abstract: 150-250 words, no \\cite.
- Do NOT include \\bibliography, \\bibliographystyle, or \\begin{{thebibliography}} — the skeleton handles these.
- Tables: use \\resizebox{{\\textwidth}}{{!}}{{...}} around wide tabular environments to prevent overflow. Keep all tables to a CONSISTENT column count and font size so reading experience is uniform across the paper.
- Long URLs: wrap with \\url{{...}} so they break across lines.
- Figures: use [htbp] placement and \\includegraphics[width=0.9\\textwidth]{{figures/<filename>}}.
- Workflow figure: if `workflow.png` appears in the **Available** figures list above (not the "Already used" list), include it ONCE in this section to show the study design. Do NOT include it if it is already listed as "Already used".
- Hyperparameter details: report only the FINAL chosen values and 1-sentence justification. Do NOT describe the full tuning search, grid details, or intermediate results.
- Visualizations: ALL charts and plots MUST be generated with seaborn or plotly. Never use single-color bar charts — use a distinct color per category/group.
- Section order: Conclusion MUST be the final section (unless an explicit Appendix follows). Never place any content section after Conclusion.

%%BEGIN SECTION%%
\\section{{{section.title()}}}
...
%%END SECTION%%

%%BEGIN BIBTEX%%
@article{{key, ...}}
%%END BIBTEX%%""",
        budget_remaining=shared.get("budget_remaining", 0))
    track_cost(shared, f"writing:{section}", usage)

    sec_m = re.search(r"%%BEGIN SECTION%%(.*?)%%END SECTION%%", text, re.DOTALL)
    body = _sanitize_section_body(sec_m.group(1) if sec_m else text)
    bib_m = re.search(r"%%BEGIN BIBTEX%%(.*?)%%END BIBTEX%%", text, re.DOTALL)
    if bib_m:
        new_entries = [e.strip() for e in re.findall(r"(@\w+\{[^@]+)", bib_m.group(1), re.DOTALL) if e.strip()]
        _extend_bibtex(shared, new_entries)

    if len(body.strip()) < MIN_SECTION_LENGTH:
        print(f"[PlanDrivenExecutor] '{section}' too short ({len(body)} chars) — retrying with strict prompt")
        retry_text, retry_usage = await call_llm_async(
            f"""IMPORTANT: You MUST output the {section} section content between the markers below.
Previous attempt produced no usable content. Do not skip this.

## Topic: {shared["topic"]}
## Artifacts
{artifact_text[:ARTIFACT_CONTEXT_CHARS]}
## BibTeX keys: {", ".join(cite_keys) or "No citations yet."}

Rules:
- Output ONLY LaTeX starting with \\section{{{section.title()}}}
- Use \\cite{{key}} only from the keys listed above
- Do NOT include bibliography or preamble

%%BEGIN SECTION%%
\\section{{{section.title()}}}
...your content here...
%%END SECTION%%""",
            budget_remaining=shared.get("budget_remaining", 0))
        track_cost(shared, f"writing:{section}:retry", retry_usage)
        retry_sec_m = re.search(r"%%BEGIN SECTION%%(.*?)%%END SECTION%%", retry_text, re.DOTALL)
        retry_body = _sanitize_section_body(retry_sec_m.group(1) if retry_sec_m else retry_text)
        if len(retry_body.strip()) > len(body.strip()):
            body = retry_body
            print(f"[PlanDrivenExecutor] '{section}' retry succeeded ({len(body)} chars)")
        else:
            print(f"[PlanDrivenExecutor] '{section}' retry also short ({len(retry_body)} chars) — keeping best")

    shared.setdefault("section_bodies", {})[section] = body
    if section not in shared.get("sections_written", []):
        shared.setdefault("sections_written", []).append(section)
    for fname in re.findall(r"\\includegraphics[^{]*\{figures/([^}]+)\}", body):
        shared.setdefault("figures_used", [])
        if fname not in shared["figures_used"]:
            shared["figures_used"].append(fname)
    if len(body.strip()) < MIN_SECTION_LENGTH:
        print(f"[PlanDrivenExecutor] WARNING: '{section}' body still empty after retry")
    print(f"[PlanDrivenExecutor] '{section}' written ({len(body)} chars, ${usage['cost']:.4f})")


async def _assemble_tex_async(shared: dict):
    """Async version of _assemble_tex — title generation is awaited."""
    await _generate_workflow_diagram_async(shared)
    order = _section_order(shared)

    cleaned_bodies = {s: _sanitize_section_body(b) for s, b in shared.get("section_bodies", {}).items()}
    body = "\n\n".join(cleaned_bodies.get(s, "")
                       for s in order if s in cleaned_bodies and s != "abstract")
    abstract = re.sub(r"\\section\{[Aa]bstract\}\s*", "",
                      cleaned_bodies.get("abstract", "Abstract not available.")).strip()
    if shared.get("paper_title"):
        title = shared["paper_title"]
    else:
        # Fallback: ReviewExecutor did not run (e.g. budget skipped review)
        title_text, title_usage = await call_llm_async(
            f"Generate a concise academic paper title (under {TITLE_MAX_WORDS} words) "
            f"based on the following paper draft. "
            f"Return ONLY the title, no quotes, no explanation.\n\n{body[:TITLE_TOPIC_CHARS]}",
            budget_remaining=shared.get("budget_remaining", 0),
        )
        track_cost(shared, "title:generate", title_usage)
        title = title_text.strip().strip('"').strip("'")
    tex = (LATEX_SKELETON
           .replace("%% TITLE %%", title)
           .replace("%% ABSTRACT %%", abstract)
           .replace("%% BODY %%", body))
    bib = dedup_bibtex(shared.get("bibtex_entries", []))
    out_dir = Path(shared["output_path"])
    (out_dir / "report.tex").write_text(tex, encoding="utf-8")
    (out_dir / "references.bib").write_text(bib, encoding="utf-8")
    shared["tex_content"] = tex
    shared["bib_content"] = bib
    print(f"[PlanDrivenExecutor] report.tex assembled ({len(tex)} chars)")


def _assemble_tex(shared: dict):
    """Assemble report.tex + references.bib from written sections."""
    order = _section_order(shared)
    cleaned_bodies = {s: _sanitize_section_body(b) for s, b in shared.get("section_bodies", {}).items()}
    body = "\n\n".join(cleaned_bodies.get(s, "")
                       for s in order if s in cleaned_bodies and s != "abstract")
    abstract = re.sub(r"\\section\{[Aa]bstract\}\s*", "",
                      cleaned_bodies.get("abstract", "Abstract not available.")).strip()
    if shared.get("paper_title"):
        title = shared["paper_title"]
    else:
        title_text, title_usage = call_llm(
            f"Generate a concise academic paper title (under {TITLE_MAX_WORDS} words) "
            f"based on the following paper draft. "
            f"Return ONLY the title, no quotes, no explanation.\n\n{body[:TITLE_TOPIC_CHARS]}",
            budget_remaining=shared.get("budget_remaining", 0),
        )
        track_cost(shared, "title:generate", title_usage)
        title = title_text.strip().strip('"').strip("'")
    tex = (LATEX_SKELETON
           .replace("%% TITLE %%", title)
           .replace("%% ABSTRACT %%", abstract)
           .replace("%% BODY %%", body))
    bib = dedup_bibtex(shared.get("bibtex_entries", []))
    out_dir = Path(shared["output_path"])
    (out_dir / "report.tex").write_text(tex, encoding="utf-8")
    (out_dir / "references.bib").write_text(bib, encoding="utf-8")
    shared["tex_content"] = tex
    shared["bib_content"] = bib
    print(f"[PlanDrivenExecutor] report.tex assembled ({len(tex)} chars)")


# ===================================================================
# 1. Initializer  — zero LLM calls, sets up shared state
# ===================================================================
class Initializer(Node):
    def prep(self, shared):
        return shared["budget_dollars"]

    def exec(self, budget):
        return _report_type(budget)

    def post(self, shared, prep_res, exec_res):
        task_id = str(uuid.uuid4())
        out_dir = Path(shared.get("output_dir", "outputs")) / task_id
        out_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("artifacts", "figures", "data", "scripts"):
            (out_dir / sub).mkdir(exist_ok=True)

        shared.update({
            "output_path": str(out_dir),
            "report_type": exec_res,
            "budget_remaining": shared["budget_dollars"],
            "artifacts": {}, "bibtex_entries": [], "history": [],
            "sections_written": [], "section_bodies": {},
            "failed_code": {}, "fix_attempts": 0,
            "cost_log": [], "review_rounds": 0,
        })

        print(f"[Initializer] {out_dir} | {exec_res} | ${shared['budget_dollars']:.2f}")
        return "research"


# ===================================================================
# 2. PlanInitialExecutor  — drafts structured todo list (feedforward control)
# ===================================================================
class PlanInitialExecutor(AsyncNode):
    async def prep_async(self, shared):
        return {
            "topic": shared["topic"],
            "report_type": shared.get("report_type", "Literature Review"),
            "skill_index": shared["skill_index"],
            "budget": shared.get("budget_remaining", 0),
            "calls_left": estimate_calls_remaining(
                shared.get("budget_remaining", 0),
                cost_log=shared.get("cost_log", []),
            ),
        }

    async def exec_async(self, prep_res):
        skills_list = "\n".join(f"- {k}: {v}" for k, v in sorted(prep_res["skill_index"].items()))
        required_sections = SECTION_ORDER.get(prep_res["report_type"],
                                              SECTION_ORDER["Literature Review"])
        text, usage = await call_llm_async(
            f"""You are a research planning agent. Draft a concrete, ordered research plan.

## Topic
{prep_res["topic"]}

## Report type: {prep_res["report_type"]}
## Budget: ${prep_res["budget"]:.4f} (~{prep_res["calls_left"]} calls left)

## Available skills
{skills_list}

## Required sections (must all be written)
{", ".join(required_sections)}

Create a focused end-to-end plan: research steps first, then one write step per section.
Prioritise breadth first (survey → analysis → synthesis → write).
Fit within budget — fewer research steps for small budgets.

Return YAML list only:
```yaml
- id: 1
  type: research
  task: <one-line description of what this step produces>
  skill: <skill-name>
- id: 2
  type: write
  task: Write the <section> section
  skill: scientific-writing
  section: <section-name>
```""",
            budget_remaining=prep_res["budget"],
        )
        return text, usage

    async def post_async(self, shared, prep_res, exec_res):
        text, usage = exec_res
        track_cost(shared, "plan:draft", usage)

        parsed = parse_yaml_response(text)
        if isinstance(parsed, list) and parsed:
            plan = []
            for i, item in enumerate(parsed):
                if not isinstance(item, dict) or not item.get("task"):
                    continue
                step = {
                    "id": item.get("id", i + 1),
                    "type": item.get("type", "research"),
                    "task": item.get("task", ""),
                    "skill": item.get("skill", ""),
                    "status": "pending",
                }
                if item.get("section"):
                    step["section"] = item["section"]
                plan.append(step)
        else:
            print(f"[PlanInitialExecutor] Could not parse plan — raw response:\n{text[:500]}")
            # Fallback: build a minimal plan from required sections using available skills
            plan = self._fallback_plan(shared, prep_res)

        shared["plan"] = plan
        if plan:
            print(f"[PlanInitialExecutor] {len(plan)}-step plan drafted:")
            for t in plan:
                label = f"[{t['type']}:{t['skill']}]"
                if t.get("section"):
                    label += f"[{t['section']}]"
                print(f"  {t['id']}. {label} {t['task']}")
        return "execute"

    def _fallback_plan(self, shared: dict, prep_res: dict) -> list:
        """Build a minimal valid plan when LLM output cannot be parsed."""
        skill_index = prep_res["skill_index"]
        required_sections = SECTION_ORDER.get(prep_res["report_type"],
                                              SECTION_ORDER["Literature Review"])
        # Pick first available research skill for a survey step
        research_skill = next(iter(skill_index), "")
        plan = []
        step_id = 1
        if research_skill:
            plan.append({
                "id": step_id,
                "type": "research",
                "task": f"Survey literature on: {prep_res['topic']}",
                "skill": research_skill,
                "status": "pending",
            })
            step_id += 1
        for section in required_sections:
            plan.append({
                "id": step_id,
                "type": "write",
                "task": f"Write the {section} section",
                "skill": "",
                "section": section,
                "status": "pending",
            })
            step_id += 1
        print(f"[PlanInitialExecutor] Using fallback plan ({len(plan)} steps).")
        return plan


# ===================================================================
# 3. PlanDrivenExecutor  — executes plan steps in order, revises plan
# ===================================================================
class PlanDrivenExecutor(AsyncNode):
    async def prep_async(self, shared):
        plan = shared.get("plan", [])
        pending = [t for t in plan if t.get("status") == "pending"]
        next_step = pending[0] if pending else None
        if next_step:
            next_step["status"] = "in_progress"
        return {
            "step": next_step,
            "budget": shared.get("budget_remaining", 0),
            "plan_context": _plan_context(shared),
            "artifact_index": _artifact_index(shared),
        }

    async def exec_async(self, prep_res):
        # No-op: actual execution happens in post_async (needs shared)
        return prep_res

    async def post_async(self, shared, prep_res, exec_res):
        step = prep_res["step"]
        budget = prep_res["budget"]

        # Plan exhausted or budget too low → review
        if not step or budget < WRITE_RESERVE:
            return "review"

        skill = step.get("skill", "")
        step_type = step.get("type", "research")

        if step_type == "write":
            section = step.get("section", "")
            if not section:
                task = step.get("task", "").lower()
                for s in _section_order(shared):
                    if s in task:
                        section = s
                        break
            if section:
                print(f"[PlanDrivenExecutor] write:{section}")
                await _write_section_async(section, shared)
                step["status"] = "done"
            else:
                print(f"[PlanDrivenExecutor] write step missing section, skipping.")
                step["status"] = "failed"
        else:
            if skill and skill in shared.get("skill_index", {}):
                print(f"[PlanDrivenExecutor] research:{skill} — {step.get('task','')}")
                exhausted_before = len(shared.get("exhausted_steps", []))
                await _run_skill_async(skill, shared)
                exhausted_after = len(shared.get("exhausted_steps", []))
                step["status"] = "failed" if exhausted_after > exhausted_before else "done"
            else:
                print(f"[PlanDrivenExecutor] unknown skill '{skill}', skipping.")
                step["status"] = "failed"

        await self._maybe_revise_plan_async(shared, step)

        pending = [t for t in shared.get("plan", []) if t.get("status") == "pending"]
        if pending and shared.get("budget_remaining", 0) >= WRITE_RESERVE:
            return "execute"
        return "review"

    async def _maybe_revise_plan_async(self, shared: dict, completed_step: dict):
        """Ask LLM if remaining plan needs adjustment based on new findings."""
        pending = [t for t in shared.get("plan", []) if t.get("status") == "pending"]
        if not pending or shared.get("budget_remaining", 0) < BUDGET_RESERVE * 2:
            return

        if completed_step.get("status") != "failed":
            done_research = [t for t in shared.get("plan", [])
                             if t.get("status") == "done" and t.get("type") == "research"]
            if len(done_research) % PLAN_REVISE_EVERY != 0:
                return

        text, usage = await call_llm_async(
            f"""You are a research plan monitor. A step just completed:
Step: {completed_step.get('task','')}
Skill: {completed_step.get('skill','')}
Status: {completed_step.get('status','')}

New artifacts:
{_artifact_index(shared)}

Remaining plan steps:
{chr(10).join(f"  {t['id']}. [{t.get('type','research')}:{t.get('skill','')}] {t['task']}" for t in pending)}

Should the remaining plan be revised? Only revise if a completed step revealed a critical gap
or made a remaining step redundant. Minor adjustments are not worth the cost.

Return YAML:
```yaml
revise: false
```
or
```yaml
revise: true
changes:
  - id: <existing-id>
    action: remove        # remove a now-redundant step
  - id: <new-id>
    action: insert
    after: <existing-id>  # insert after this step
    type: research
    task: <one-line description>
    skill: <skill-name>
```""",
            budget_remaining=shared.get("budget_remaining", 0),
        )
        track_cost(shared, "plan:revise", usage)

        parsed = parse_yaml_response(text)
        if not isinstance(parsed, dict) or not parsed.get("revise"):
            return

        plan = shared.get("plan", [])
        changes = parsed.get("changes", [])
        for change in changes:
            action = change.get("action")
            if action == "remove":
                cid = int(change["id"]) if change.get("id") is not None else None
                plan[:] = [t for t in plan if int(t.get("id", 0)) != cid or t.get("status") != "pending"]
                print(f"[PlanDrivenExecutor] plan: removed step {cid}")
            elif action == "insert":
                after_id = int(change["after"]) if change.get("after") is not None else None
                new_task = change.get("task", "")
                new_skill = change.get("skill", "")
                # Dedup: skip if an equivalent pending/done step already exists
                sig = (new_skill, new_task[:60].lower())
                duplicate = any(
                    (t.get("skill", ""), t.get("task", "")[:60].lower()) == sig
                    for t in plan
                )
                if duplicate:
                    print(f"[PlanDrivenExecutor] plan: skipping duplicate insert '{new_task[:60]}'")
                    continue
                new_step = {
                    "id": change.get("id", max((int(t["id"]) for t in plan), default=0) + 1),
                    "type": change.get("type", "research"),
                    "task": new_task,
                    "skill": new_skill,
                    "status": "pending",
                }
                if change.get("section"):
                    new_step["section"] = change["section"]
                idx = next((i for i, t in enumerate(plan) if int(t.get("id", -1)) == after_id), len(plan) - 1)
                plan.insert(idx + 1, new_step)
                print(f"[PlanDrivenExecutor] plan: inserted step '{new_step['task'][:60]}'")
        shared["plan"] = plan


# ===================================================================
# 4. ReviewExecutor  — runs peer-review skill against assembled draft
# ===================================================================
class ReviewExecutor(AsyncNode):
    async def prep_async(self, shared):
        # Gap check: ensure all required sections have substantive content before review.
        # If any are missing or too short, inject write steps back into the plan and
        # execute them now so the reviewer sees a complete draft.
        required = _required_sections(shared)
        section_bodies = shared.get("section_bodies", {})
        budget = shared.get("budget_remaining", 0)
        gap_sections = [
            s for s in required
            if len(section_bodies.get(s, "").strip()) < 200
        ]
        if gap_sections and budget >= WRITE_RESERVE:
            print(f"[ReviewExecutor] Gap check: missing/short sections: {gap_sections}")
            plan = shared.get("plan", [])
            next_id = max((t["id"] for t in plan), default=0) + 1
            for s in gap_sections:
                # Only inject if not already a pending write step for this section
                already_pending = any(
                    t.get("type") == "write" and t.get("section") == s and t.get("status") == "pending"
                    for t in plan
                )
                if not already_pending:
                    plan.append({
                        "id": next_id,
                        "type": "write",
                        "task": f"Write the {s} section",
                        "skill": "",
                        "section": s,
                        "status": "pending",
                    })
                    next_id += 1
            shared["plan"] = plan
            # Execute the gap-fill steps immediately
            for step in [t for t in shared["plan"] if t.get("status") == "pending" and t.get("type") == "write"]:
                if shared.get("budget_remaining", 0) < WRITE_RESERVE:
                    break
                section = step.get("section", "")
                if section in gap_sections:
                    print(f"[ReviewExecutor] Gap-filling section: {section}")
                    await _write_section_async(section, shared)
                    step["status"] = "done"

        # Assemble .tex before review so reviewer sees the full draft
        await _assemble_tex_async(shared)
        return {
            "topic": shared["topic"],
            "report_type": shared.get("report_type", "Literature Review"),
            "budget": shared.get("budget_remaining", 0),
            "tex_content": shared.get("tex_content", ""),
            "artifact_index": _artifact_index(shared),
            "skill_index": shared.get("skill_index", {}),
            "review_rounds": shared.get("review_rounds", 0),
            "max_rounds": int(os.environ.get("MAX_REVIEW_ROUNDS", "1")),
        }

    async def exec_async(self, prep_res):
        budget = prep_res["budget"]

        # Skip review if budget too low or max rounds reached — sync quality gate
        if budget < REVIEW_RESERVE or prep_res["review_rounds"] >= prep_res["max_rounds"]:
            return {"action": "compile", "comments": []}, \
                   {"input_tokens": 0, "output_tokens": 0, "cost": 0}

        text, usage = await call_llm_async(
            f"""You are a rigorous peer reviewer assessing a draft for top-venue submission.
Evaluate against NeurIPS/ICML/ICLR/ACL reviewer standards.

## Topic: {prep_res["topic"]}
## Report type: {prep_res["report_type"]}

## Draft (LaTeX)
{prep_res["tex_content"][:6000]}

## Artifacts available
{prep_res["artifact_index"]}

## Evaluation dimensions (score each 1-5 mentally, flag if < 3)
- **Soundness**: Claims well-supported by evidence? Methods technically correct? Assumptions stated?
- **Significance**: Advances understanding? Others will build on it?
- **Originality**: New insights, methods, or perspectives? Differentiated from prior work?
- **Clarity**: Well-organized? Reproducible from description alone?

## Quality checklist — flag any violated item as a major comment
### Structure
- Title specific and under 15 words (not all-lowercase)
- Abstract 150-250 words, self-contained, states problem/approach/findings
- Introduction ends with 1-3 specific claims
- Background synthesizes by theme (not chronological list)
- Every claim backed by citation or evidence
- Limitations honestly acknowledged
- Conclusion does not repeat abstract

### Figures & formulas
- At least one figure or table (architecture, workflow, or results plot)
- Study workflow diagram (`figures/workflow.png`) included in Introduction or Methods
- All figures referenced in text with self-contained captions
- Key technical concepts formalized with equations where appropriate
- All charts/plots use seaborn or plotly; no single-color bar charts (each category has a distinct color)

### Tables & hyperparameters
- All tables use consistent column count and font size across the paper
- Hyperparameter reporting limited to final values + 1-sentence justification; no tuning grids or search details

### Section order
- Conclusion is the LAST section (no content section may follow it except an explicit Appendix)

### Citations
- Min 5 (quick summary), 10-15 (lit review), 20+ (full paper)
- Every \\cite{{key}} resolves; every .bib entry is cited
- At least 30% from last 5 years

### Writing craft
- Active voice preferred; formal academic tone
- Narrative follows claim-evidence-commentary pattern
- No overclaiming or speculation presented as fact
- Related work organized by theme, positioned against this contribution

## Available skills for research fixes (use ONLY these exact skill names in the `skill` field)
{format_skill_index(prep_res["skill_index"])}

Return YAML:
```yaml
action: compile       # draft meets submission standard
# or
action: revise        # critical issues found
comments:
  - id: 1
    severity: major   # major | minor
    section: <section-name>
    issue: <specific problem, cite checklist item>
    fix: research     # needs new data/analysis
    # or
    fix: rewrite      # section prose rewrite only
    skill: <skill-name>   # only when fix==research
```
Be strict but realistic. Only flag major issues that materially improve the paper.
Do NOT request revision for cosmetic issues — only for checklist violations or missing substance.""",
            budget_remaining=budget)
        parsed = parse_yaml_response(text)
        if not isinstance(parsed, dict):
            return {"action": "compile", "comments": []}, usage
        return parsed, usage

    async def post_async(self, shared, prep_res, exec_res):
        decision, usage = exec_res
        track_cost(shared, f"review:{prep_res['review_rounds']}", usage)
        shared["review_rounds"] = prep_res["review_rounds"] + 1

        comments = decision.get("comments", [])
        major = [c for c in comments if isinstance(c, dict) and c.get("severity") == "major"]
        addressed = shared.get("addressed_comments", [])
        pending = [c for c in major if c.get("id") not in addressed]

        # Sync quality gate: decide compile vs revise based on review results
        if decision.get("action") == "compile" or not pending:
            print(f"[ReviewExecutor] Draft accepted (round {shared['review_rounds']})")
            # Generate title now — from the final accepted draft body, not just the topic
            if not shared.get("paper_title"):
                order = _section_order(shared)
                cleaned = {s: _sanitize_section_body(b)
                           for s, b in shared.get("section_bodies", {}).items()}
                draft_body = "\n\n".join(cleaned.get(s, "") for s in order
                                         if s in cleaned and s != "abstract")
                title_context = (draft_body or shared.get("topic", ""))[:TITLE_TOPIC_CHARS]
                title_text, title_usage = await call_llm_async(
                    f"Generate a concise academic paper title (under {TITLE_MAX_WORDS} words) "
                    f"based on the following paper draft. "
                    f"Return ONLY the title, no quotes, no explanation.\n\n{title_context}",
                    budget_remaining=shared.get("budget_remaining", 0),
                )
                track_cost(shared, "title:generate", title_usage)
                shared["paper_title"] = title_text.strip().strip('"').strip("'")
                print(f"[ReviewExecutor] Title generated: {shared['paper_title']}")
            return "compile"

        # Append revision steps to plan tail
        plan = shared.get("plan", [])
        valid_skills = set(shared.get("skill_index", {}).keys())
        next_id = max((t["id"] for t in plan), default=0) + 1
        for comment in pending:
            comment_id = comment.get("id")
            shared.setdefault("addressed_comments", []).append(comment_id)
            fix = comment.get("fix", "rewrite")
            section = comment.get("section", "")
            skill = comment.get("skill", "")
            print(f"[ReviewExecutor] #{comment_id} [{section}]: {comment.get('issue','')[:80]}")
            # Validate skill ID — if not in index, downgrade to rewrite
            if fix == "research" and skill and skill in valid_skills:
                plan.append({"id": next_id, "type": "research", "skill": skill,
                             "task": f"Additional research for {section}: {comment.get('issue','')[:60]}",
                             "status": "pending"})
                print(f"[ReviewExecutor] → appended research:{skill}")
            else:
                if fix == "research" and skill and skill not in valid_skills:
                    print(f"[ReviewExecutor] → invalid skill '{skill}', downgrading to rewrite:{section}")
                plan.append({"id": next_id, "type": "write", "skill": "",
                             "section": section,
                             "task": f"Rewrite {section}: {comment.get('issue','')[:60]}",
                             "status": "pending"})
                print(f"[ReviewExecutor] → appended write:{section}")
            next_id += 1
        shared["plan"] = plan
        return "execute"


# ===================================================================
# 6. CompileTeX  — PDF generation (runs exactly once, final step)
# ===================================================================
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


# ===================================================================
# 7. FixTeX  — fix LaTeX errors or missing citations (max 2 attempts)
# ===================================================================
class FixTeX(AsyncNode):
    async def prep_async(self, shared):
        undefined = shared.get("undefined_citations", [])
        return {
            "tex_content": shared["tex_content"],
            "bib_content": shared.get("bib_content", ""),
            "errors": shared.get("compile_errors", ""),
            "attempt": shared.get("fix_attempts", 0),
            "undefined_citations": undefined,
            "mode": "citation" if undefined else "latex_error",
            "budget_remaining": shared.get("budget_remaining", 0),
        }

    async def exec_async(self, prep_res):
        if prep_res["attempt"] >= 2:
            return None, {"input_tokens": 0, "output_tokens": 0, "cost": 0}

        if prep_res["mode"] == "citation":
            prompt = f"""Generate BibTeX entries for these undefined citation keys:
{", ".join(prep_res["undefined_citations"])}

Current .bib (do NOT repeat existing entries):
{prep_res["bib_content"][:3000]}

Return ONLY new BibTeX entries. Each MUST have: author, title, year, journal/booktitle.
Use the cite keys EXACTLY as listed."""
        else:
            errors = "\n".join(l for l in prep_res["errors"].split("\n")
                               if l.startswith("!") or "Error" in l or "Undefined" in l)[:1000]
            prompt = f"""Fix these LaTeX compilation errors. Return the COMPLETE corrected .tex file.

Errors:
{errors}

Current .tex:
{prep_res["tex_content"]}

Rules: do not change \\documentclass or \\usepackage lines.
Common fixes: escape %, &, #, $, _; close environments; fix undefined commands."""

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


# ===================================================================
# 8. Finisher  — persist summary, print report
# ===================================================================
class Finisher(Node):
    def post(self, shared, prep_res, exec_res):
        from datetime import datetime, timezone
        total = sum(e["cost"] for e in shared.get("cost_log", []))
        out_dir = Path(shared["output_path"])
        (out_dir / "cost_log.json").write_text(
            json.dumps(shared.get("cost_log", []), indent=2, ensure_ascii=False), encoding="utf-8")
        (out_dir / "summary.json").write_text(
            json.dumps({
                "topic": shared.get("topic", ""),
                "report_type": shared.get("report_type", ""),
                "budget_dollars": shared.get("budget_dollars", 0),
                "total_cost": round(total, 6),
                "budget_remaining": round(shared.get("budget_remaining", 0), 6),
                "steps_executed": len(shared.get("history", [])),
                "artifacts": list(shared.get("artifacts", {}).keys()),
                "bibtex_count": len(shared.get("bibtex_entries", [])),
                "review_rounds": shared.get("review_rounds", 0),
                "fix_attempts": shared.get("fix_attempts", 0),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n{'='*50}")
        print(f"Done! Total: ${total:.4f} / ${shared['budget_dollars']:.2f}")
        print(f"Output: {shared['output_path']}/")
        print(f"{'='*50}")
