"""PocketFlow nodes for the Autonomous Scientist agent.

Full pipeline:
  Initializer → ResearchExecutor (loop) → WritingExecutor (loop)
              → ReviewExecutor → [research / write / compile]
              → CompileTeX ↔ FixTeX → Finisher

ReviewExecutor dispatches revisions directly into the research/writing loops.
LaTeX compilation happens exactly once, as the final PDF generation step.
"""

import json
import os
import re
import subprocess
import uuid
from pathlib import Path

from pocketflow import Node

from .utils import (
    call_llm,
    call_llm_with_tools,
    format_skill_index,
    format_available_keys,
    load_skill_content,
    parse_yaml_response,
    extract_bibtex,
    dedup_bibtex,
    track_cost,
    estimate_calls_remaining,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BUDGET_RESERVE  = 0.03   # minimum budget to enter writing phase
WRITE_RESERVE   = 0.015  # minimum budget to enter review phase
REVIEW_RESERVE  = 0.008  # minimum budget to trigger revision (else compile)

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
\usepackage{hyperref}
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
    if budget < 0.10:  return "Quick Summary"
    if budget < 0.50:  return "Literature Review"
    if budget < 2.00:  return "Research Report"
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
    active = [t for t in plan if t.get("status") == "in_progress"]
    pending = [t for t in plan if t.get("status") == "pending"]

    lines = [f"## Research Plan ({len(done)}/{len(plan)} steps complete)"]

    # Lookback: last min(lookback_n, len(done)) completed steps
    shown_done = done[-lookback_n:] if done else []
    if shown_done:
        lines.append("### Completed (recent)")
        for t in shown_done:
            lines.append(f"  [x] {t['id']}. {t['task']}")
        if len(done) > lookback_n:
            lines.append(f"  ... ({len(done) - lookback_n} earlier steps)")

    # Active step
    for t in active:
        lines.append(f"  [>] {t['id']}. {t['task']}  ← current")

    # Lookahead: remaining steps (all of them — naturally shrinks to 1-2 near end)
    if pending:
        lines.append("### Upcoming")
        for t in pending:
            lines.append(f"  [ ] {t['id']}. {t['task']}")

    return "\n".join(lines)


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
                               text=True, errors="replace", timeout=300,
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
    shared["bibtex_entries"].extend(entries)

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

    # Decompose
    text, usage = call_llm(
        f"""Decompose this research skill into 2-5 atomic steps.

## Topic: {shared["topic"]}
## Skill: {skill_name}
## Instructions:
{skill_content[:1500]}

Return YAML list only:
```yaml
- step: 1
  instruction: <one focused action, under 30 words>
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

        step_prompt = f"""Execute this single research step. Be focused and concise.

## Topic: {shared["topic"]}
## Step: {instruction}
## Recent context: {_recent_history(shared, 5)}
## Working directory: {shared["output_path"]}

Save data → `data/`, figures → `figures/` (relative paths). No plt.show().
After completing the step, summarise findings and append any citations:

%%BEGIN BIBTEX%%
@article{{key, author={{...}}, title={{...}}, journal={{...}}, year={{YYYY}}}}
%%END BIBTEX%%"""

        if needs_code:
            # Use tool-calling loop: model drives bash execution with error feedback.
            step_text, step_usage = call_llm_with_tools(
                step_prompt,
                budget_remaining=shared.get("budget_remaining", 0),
                cwd=shared["output_path"],
            )
            code_outputs = []  # execution happened inside the tool loop
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
    artifact_text = "\n\n".join(f"### {k}\n{v[:1200]}"
                                for k, v in shared.get("artifacts", {}).items())
    prior_text = ("\n\n## Prior sections\n" +
                  "\n\n".join(f"### {s}\n{b[:400]}"
                              for s, b in shared.get("section_bodies", {}).items())
                  if shared.get("section_bodies") else "")
    figures_dir = out_dir / "figures"
    figure_files = ([f.name for f in sorted(figures_dir.iterdir())
                     if f.suffix.lower() in (".png", ".pdf", ".jpg", ".jpeg")]
                    if figures_dir.is_dir() else [])
    figures_block = ""
    if figure_files and section in ("results", "discussion", "methods"):
        figures_block = ("\n\n## Figures\n" + "\n".join(f"- {f}" for f in figure_files) +
                         r"\n Pattern: \begin{figure}[htbp]\centering"
                         r"\includegraphics[width=0.8\textwidth]{figures/<name>}"
                         r"\caption{...}\label{fig:<l>}\end{figure}")
    data_dir = out_dir / "data"
    data_block = ""
    if data_dir.is_dir() and section in ("results", "methods"):
        previews = []
        for f in sorted(data_dir.iterdir()):
            if f.is_file() and f.suffix.lower() in (".csv", ".json", ".tsv"):
                try:
                    previews.append(f"### {f.name}\n```\n{f.read_text(encoding='utf-8', errors='replace')[:500]}\n```")
                except Exception:
                    pass
        if previews:
            data_block = "\n\n## Data\n" + "\n".join(previews)

    text, usage = call_llm(
        f"""Write the **{section}** section of a {shared.get("report_type","Literature Review").lower()} as compilable LaTeX.

## Topic: {shared["topic"]}
## Artifacts
{artifact_text}{prior_text}{figures_block}{data_block}

## BibTeX keys: {", ".join(cite_keys) or "No citations yet."}

## Rules
- Output ONLY this section's LaTeX (\\section{{...}} onward). No preamble.
- Use \\cite{{key}} only from keys above. Back every claim.
- Active voice, formal tone. Escape \\%, \\&, \\#, \\$.
- Abstract: 150-250 words, no \\cite.

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
    body = sec_m.group(1).strip() if sec_m else text.strip()
    bib_m = re.search(r"%%BEGIN BIBTEX%%(.*?)%%END BIBTEX%%", text, re.DOTALL)
    if bib_m:
        new_entries = [e.strip() for e in re.findall(r"(@\w+\{[^@]+)", bib_m.group(1), re.DOTALL) if e.strip()]
        shared.setdefault("bibtex_entries", []).extend(new_entries)

    shared.setdefault("section_bodies", {})[section] = body
    if section not in shared.get("sections_written", []):
        shared.setdefault("sections_written", []).append(section)
    print(f"[WritingExecutor] '{section}' written ({len(body)} chars, ${usage['cost']:.4f})")


def _assemble_tex(shared: dict):
    """Assemble report.tex + references.bib from written sections."""
    order = _section_order(shared)
    body = "\n\n".join(shared["section_bodies"].get(s, "")
                       for s in order if s in shared["section_bodies"] and s != "abstract")
    abstract = re.sub(r"\\section\{[Aa]bstract\}\s*", "",
                      shared["section_bodies"].get("abstract", "Abstract not available.")).strip()
    tex = (LATEX_SKELETON
           .replace("%% TITLE %%", shared.get("topic", "Research Report"))
           .replace("%% ABSTRACT %%", abstract)
           .replace("%% BODY %%", body))
    bib = dedup_bibtex(shared.get("bibtex_entries", []))
    out_dir = Path(shared["output_path"])
    (out_dir / "report.tex").write_text(tex, encoding="utf-8")
    (out_dir / "references.bib").write_text(bib, encoding="utf-8")
    shared["tex_content"] = tex
    shared["bib_content"] = bib
    print(f"[WritingExecutor] report.tex assembled ({len(tex)} chars)")


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
            "revision_scope": {},
        })

        print(f"[Initializer] {out_dir} | {exec_res} | ${shared['budget_dollars']:.2f}")
        return "research"


# ===================================================================
# 2. PlanExecutor  — drafts structured todo list (feedforward control)
# ===================================================================
class PlanExecutor(Node):
    def prep(self, shared):
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

    def exec(self, prep_res):
        skills_list = "\n".join(f"- {k}: {v}" for k, v in sorted(prep_res["skill_index"].items()))
        text, usage = call_llm(
            f"""You are a research planning agent. Draft a concrete, ordered research plan.

## Topic
{prep_res["topic"]}

## Report type: {prep_res["report_type"]}
## Budget: ${prep_res["budget"]:.4f} (~{prep_res["calls_left"]} calls left)

## Available skills
{skills_list}

Create a focused research plan: 3-7 steps, each using one skill.
Prioritise breadth first (survey → analysis → synthesis).
Fit within budget — fewer steps for small budgets.

Return YAML list only:
```yaml
- id: 1
  task: <one-line description of what this step produces>
  skill: <skill-name>
- id: 2
  ...
```""",
            budget_remaining=prep_res["budget"],
        )
        return text, usage

    def post(self, shared, prep_res, exec_res):
        text, usage = exec_res
        track_cost(shared, "plan:draft", usage)

        parsed = parse_yaml_response(text)
        if isinstance(parsed, list) and parsed:
            plan = [
                {"id": item.get("id", i + 1),
                 "task": item.get("task", ""),
                 "skill": item.get("skill", ""),
                 "status": "pending"}
                for i, item in enumerate(parsed)
                if isinstance(item, dict) and item.get("task")
            ]
        else:
            # Fallback: empty plan — ResearchExecutor will free-choose skills
            plan = []

        shared["plan"] = plan
        if plan:
            print(f"[PlanExecutor] {len(plan)}-step plan drafted:")
            for t in plan:
                print(f"  {t['id']}. [{t['skill']}] {t['task']}")
        else:
            print("[PlanExecutor] Could not parse plan — ResearchExecutor will choose freely.")
        return "research"


# ===================================================================
# 3. ResearchExecutor  — research loop
# ===================================================================
class ResearchExecutor(Node):
    @property
    def LOOKBACK(self):
        return int(os.environ.get("LOOKBACK", "3"))

    def prep(self, shared):
        budget = shared.get("budget_remaining", 0)
        cost_log = shared.get("cost_log", [])
        scope = shared.get("revision_scope", {})
        plan = shared.get("plan", [])

        # Advance plan: mark first pending item as in_progress if nothing is active
        active = [t for t in plan if t.get("status") == "in_progress"]
        pending = [t for t in plan if t.get("status") == "pending"]
        if not active and pending:
            pending[0]["status"] = "in_progress"

        # In scoped (revision) mode, suggest the revision skill from scope
        scoped_skill = scope.get("target_skill", "") if scope.get("mode") == "research" else ""

        # Determine suggested skill from plan (active item)
        active_now = [t for t in plan if t.get("status") == "in_progress"]
        plan_skill = active_now[0].get("skill", "") if active_now else ""

        return {
            "topic": shared["topic"],
            "budget": budget,
            "cost_log": cost_log,
            "calls_left": estimate_calls_remaining(budget, cost_log=cost_log),
            "skills": format_skill_index(shared["skill_index"]),
            "api_keys": format_available_keys(shared.get("api_keys", {})),
            "artifact_index": _artifact_index(shared),
            "history_text": _recent_history(shared, self.LOOKBACK),
            "plan_context": _plan_context(shared),
            "plan_skill": plan_skill,
            "skills_dir": shared["skills_dir"],
            "scoped_skill": scoped_skill,
        }

    def exec(self, prep_res):
        budget = prep_res["budget"]
        if budget < BUDGET_RESERVE:
            return {"action": "done", "reason": "budget at reserve"}, {"input_tokens": 0, "output_tokens": 0, "cost": 0}

        # Scoped mode: directed single skill, no LLM decision needed
        if prep_res["scoped_skill"]:
            return {"action": "execute", "skill": prep_res["scoped_skill"],
                    "reason": "revision-directed"}, {"input_tokens": 0, "output_tokens": 0, "cost": 0}

        plan_hint = (f"\n## Plan suggestion\nThe plan recommends: `{prep_res['plan_skill']}` next.\n"
                     f"Follow it unless the artifacts clearly show a better gap to fill.\n"
                     if prep_res["plan_skill"] else "")

        text, usage = call_llm(
            f"""You are an autonomous research agent. Choose the single best next action.

## Topic
{prep_res["topic"]}

## Budget: ${prep_res["budget"]:.4f} (~{prep_res["calls_left"]} calls left)
Reserve ${BUDGET_RESERVE:.2f} for writing + review.
{plan_hint}
{prep_res["plan_context"]}

## Available Skills
{prep_res["skills"]}

## API Keys
{prep_res["api_keys"]}

## Artifacts so far
{prep_res["artifact_index"]}

## Recent history (last {self.LOOKBACK})
{prep_res["history_text"]}

Return YAML — exactly one of:
```yaml
action: execute
skill: <skill-name>
reason: <one line>
```
or
```yaml
action: done
reason: <why research is complete>
```
Rules:
- Choose `done` (early stop) if research quality is already sufficient: key concepts covered,
  enough citations gathered, no obvious gaps remaining — even if budget is not exhausted.
- Choose `done` when budget nears reserve (${BUDGET_RESERVE:.2f} remaining).
- Otherwise choose `execute` with the skill that fills the biggest remaining gap.
Only choose skills whose required API keys are available.""",
            budget_remaining=budget)
        parsed = parse_yaml_response(text)
        return (parsed if isinstance(parsed, dict) else {"action": "done"}), usage

    def post(self, shared, prep_res, exec_res):
        decision, usage = exec_res
        track_cost(shared, "research:decide", usage)
        scope = shared.get("revision_scope", {})

        if decision.get("action") == "execute":
            skill = decision.get("skill", "")
            if skill and skill in shared.get("skill_index", {}):
                print(f"[ResearchExecutor] → {skill}: {decision.get('reason','')}")
                _run_skill(skill, shared)
                # Mark matching plan item done
                for t in shared.get("plan", []):
                    if t.get("status") == "in_progress" and t.get("skill") == skill:
                        t["status"] = "done"
                        break
            else:
                print(f"[ResearchExecutor] Unknown skill '{skill}', ending research.")

        # After scoped execution: clear scope, go to write
        if scope.get("mode") == "research":
            shared["revision_scope"] = {}
            return "write"

        if decision.get("action") == "done" or shared.get("budget_remaining", 0) < BUDGET_RESERVE:
            print(f"[ResearchExecutor] Research complete: {decision.get('reason','')}")
            return "write"
        return "research"


# ===================================================================
# 3. WritingExecutor  — writing loop
# ===================================================================
class WritingExecutor(Node):
    def prep(self, shared):
        budget = shared.get("budget_remaining", 0)
        cost_log = shared.get("cost_log", [])
        required = _required_sections(shared)
        remaining = [s for s in required if s not in shared.get("sections_written", [])]
        scope = shared.get("revision_scope", {})
        return {
            "topic": shared["topic"],
            "budget": budget,
            "cost_log": cost_log,
            "calls_left": estimate_calls_remaining(budget, cost_log=cost_log),
            "report_type": shared.get("report_type", "Literature Review"),
            "required": required,
            "remaining": remaining,
            "artifact_index": _artifact_index(shared),
            "sections_written": shared.get("sections_written", []),
            "scoped_section": scope.get("target_section", "") if scope.get("mode") == "write" else "",
        }

    def exec(self, prep_res):
        budget = prep_res["budget"]
        remaining = prep_res["remaining"]

        if not remaining or budget < WRITE_RESERVE:
            return {"action": "done"}, {"input_tokens": 0, "output_tokens": 0, "cost": 0}

        # Scoped mode: rewrite one specific section
        if prep_res["scoped_section"]:
            return {"action": "write", "section": prep_res["scoped_section"]}, \
                   {"input_tokens": 0, "output_tokens": 0, "cost": 0}

        text, usage = call_llm(
            f"""Writing a {prep_res["report_type"].lower()} on: {prep_res["topic"]}

Sections needed:  {", ".join(prep_res["required"])}
Already written:  {", ".join(prep_res["sections_written"]) or "none"}
Remaining:        {", ".join(remaining)}

Artifacts:
{prep_res["artifact_index"]}

Budget: ${budget:.4f} (~{prep_res["calls_left"]} calls left)

Which section next? Return YAML:
```yaml
action: write
section: <section-name>
```
or if complete:
```yaml
action: done
```""",
            budget_remaining=budget)
        parsed = parse_yaml_response(text)
        return (parsed if isinstance(parsed, dict) else {"action": "write", "section": remaining[0]}), usage

    def post(self, shared, prep_res, exec_res):
        decision, usage = exec_res
        track_cost(shared, "writing:decide", usage)
        scope = shared.get("revision_scope", {})

        if decision.get("action") == "write":
            section = decision.get("section") or prep_res["remaining"][0]
            _write_section(section, shared)

        # After scoped execution: clear scope, go to review
        if scope.get("mode") == "write":
            shared["revision_scope"] = {}
            return "review"

        # Check if all required sections done
        remaining_now = [s for s in prep_res["required"]
                         if s not in shared.get("sections_written", [])]
        if not remaining_now or shared.get("budget_remaining", 0) < WRITE_RESERVE:
            return "review"
        return "write"


# ===================================================================
# 4. ReviewExecutor  — runs peer-review skill against assembled draft
# ===================================================================
class ReviewExecutor(Node):
    def prep(self, shared):
        # Assemble .tex before review so reviewer sees the full draft
        _assemble_tex(shared)
        return {
            "topic": shared["topic"],
            "report_type": shared.get("report_type", "Literature Review"),
            "budget": shared.get("budget_remaining", 0),
            "tex_content": shared.get("tex_content", ""),
            "artifact_index": _artifact_index(shared),
            "review_rounds": shared.get("review_rounds", 0),
            "max_rounds": int(os.environ.get("MAX_REVIEW_ROUNDS", "1")),
        }

    def exec(self, prep_res):
        budget = prep_res["budget"]

        # Skip review if budget too low or max rounds reached
        if budget < REVIEW_RESERVE or prep_res["review_rounds"] >= prep_res["max_rounds"]:
            return {"action": "compile", "comments": []}, \
                   {"input_tokens": 0, "output_tokens": 0, "cost": 0}

        text, usage = call_llm(
            f"""You are a rigorous peer reviewer. Review this draft and identify issues.

## Topic: {prep_res["topic"]}
## Report type: {prep_res["report_type"]}
## Budget remaining: ${budget:.4f}

## Draft (LaTeX)
{prep_res["tex_content"][:6000]}

## Artifacts available
{prep_res["artifact_index"]}

Evaluate: scientific soundness, completeness, evidence quality, section structure, citation coverage.

Return YAML:
```yaml
action: compile       # if draft is acceptable for submission
# or
action: revise        # if critical issues found
comments:
  - id: 1
    severity: major   # major | minor
    section: <section>
    issue: <what is wrong>
    fix: research     # needs new data/analysis
    # or
    fix: rewrite      # section rewrite only
    skill: <skill-name if fix==research>
```
Be strict but realistic. Only request revision if it materially improves the paper.""",
            budget_remaining=budget)
        parsed = parse_yaml_response(text)
        if not isinstance(parsed, dict):
            return {"action": "compile", "comments": []}, usage
        return parsed, usage

    def post(self, shared, prep_res, exec_res):
        decision, usage = exec_res
        track_cost(shared, f"review:{prep_res['review_rounds']}", usage)
        shared["review_rounds"] = prep_res["review_rounds"] + 1

        comments = decision.get("comments", [])
        major = [c for c in comments if isinstance(c, dict) and c.get("severity") == "major"]
        addressed = shared.get("addressed_comments", [])
        pending = [c for c in major if c.get("id") not in addressed]

        if decision.get("action") == "compile" or not pending:
            print(f"[ReviewExecutor] Draft accepted (round {shared['review_rounds']})")
            return "compile"

        # Store all major comments for tracking
        shared.setdefault("review_comments", []).extend(
            c for c in major if c.get("id") not in [x.get("id") for x in shared.get("review_comments", [])]
        )

        # Dispatch the top pending comment
        comment = pending[0]
        comment_id = comment.get("id")
        shared.setdefault("addressed_comments", []).append(comment_id)
        fix = comment.get("fix", "rewrite")
        section = comment.get("section", "")
        skill = comment.get("skill", "")

        print(f"[ReviewExecutor] #{comment_id} [{section}]: {comment.get('issue','')[:80]}")

        if fix == "research" and skill:
            shared["revision_scope"] = {"mode": "research", "target_skill": skill,
                                        "target_section": section}
            print(f"[ReviewExecutor] → research: {skill}")
            return "research"

        shared["revision_scope"] = {"mode": "write", "target_section": section}
        print(f"[ReviewExecutor] → rewrite: {section}")
        return "write"


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
                               text=True, errors="replace", timeout=60)
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
class FixTeX(Node):
    def prep(self, shared):
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

    def exec(self, prep_res):
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

        return call_llm(prompt, budget_remaining=prep_res["budget_remaining"])

    def post(self, shared, prep_res, exec_res):
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
