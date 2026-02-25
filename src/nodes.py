"""PocketFlow nodes for the Autonomous Scientist agent."""

import os
import re
import subprocess
import uuid
from collections import Counter
from pathlib import Path

from pocketflow import Node

from .utils import (
    call_llm,
    format_skill_index,
    format_available_keys,
    load_skill_content,
    load_quality_standard,
    parse_yaml_response,
    extract_bibtex,
    dedup_bibtex,
    track_cost,
)

# ---------------------------------------------------------------------------
# Budget reserves — enough for WriteTeX + CompileTeX + one FixTeX round
# ---------------------------------------------------------------------------
BUDGET_RESERVE = 0.03  # dollars


# ---------------------------------------------------------------------------
# LaTeX skeleton — hardcoded, known-compilable with pdflatex + natbib
# ---------------------------------------------------------------------------
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


# ===================================================================
# 1. BudgetPlanner
# ===================================================================
class BudgetPlanner(Node):
    """Analyze topic + budget → produce a prioritized research plan."""

    def prep(self, shared):
        return {
            "topic": shared["topic"],
            "budget": shared["budget_dollars"],
            "skills": format_skill_index(shared["skill_index"]),
            "quality_standard": shared.get("quality_standard", ""),
            "api_keys": format_available_keys(shared.get("api_keys", {})),
        }

    def exec(self, prep_res):
        # Extract adaptive structure section from quality standard if available
        quality_guidance = ""
        qs = prep_res["quality_standard"]
        if qs:
            # Pull Section 2.3 (adaptive structure) and Section 4 (citation quality)
            import re as _re
            adaptive = _re.search(
                r"### 2\.3 Adaptive Structure by Report Type.*?(?=\n## |\n### [^2]|\Z)",
                qs, _re.DOTALL,
            )
            citation = _re.search(
                r"## 4\. Citation Quality.*?(?=\n## [^4]|\Z)",
                qs, _re.DOTALL,
            )
            parts = []
            if adaptive:
                parts.append(adaptive.group(0).strip())
            if citation:
                parts.append(citation.group(0).strip())
            if parts:
                quality_guidance = "\n\n## Paper Quality Standard (excerpt)\n" + "\n\n".join(parts)

        prompt = f"""You are a research planning assistant. Given a research topic and a dollar budget for LLM inference, produce an ordered plan of research skills to execute.

## Research Topic
{prep_res["topic"]}

## Budget
${prep_res["budget"]:.2f} USD

## Cost Model
Each skill execution costs roughly $0.003-$0.005.
Each planning/decision step costs roughly $0.001.
The final LaTeX report generation costs roughly $0.01.
Always reserve $0.03 for the final report + compilation.
IMPORTANT: Plan MANY steps to use the budget effectively. A $1 budget supports ~200 skill calls. A $20 budget supports ~4000 skill calls.

## Available API Keys
{prep_res["api_keys"]}

Only plan skills whose required API keys are available. Do not plan skills that depend on missing keys.

## Available Skills
{prep_res["skills"]}

## Budget Strategy Guidelines
- Budget < $0.10: research-lookup (1 call), then write report (Quick Summary).
- Budget $0.10-$0.50: research-lookup (2-3 calls on subtopics) + literature-review, then write report (Literature Review).
- Budget $0.50-$2.00: research-lookup (3-5 calls) + literature-review + hypothesis-generation + scientific-critical-thinking. Plan 10-20 steps (Research Report).
- Budget $2.00-$5.00: Plan 30-50 steps. Multiple research-lookup calls (8-15, each on different subtopics), 3-5 literature-review passes on subtopics, 3-5 github-mining or data collection passes, 4-6 statistical-analysis passes, 3-4 data-visualization passes, hypothesis-generation, scientific-critical-thinking, peer-review, citation-management, venue-templates (Full Paper).
- Budget $5.00-$10.00: Plan 50-80 steps. All of the above with MORE repetitions. 15-20 research-lookup, 5-8 literature-review, 6-10 data collection, 6-10 statistical-analysis, 5-8 data-visualization. Use the budget to build deep, comprehensive research.
- Budget $10.00+: Plan 80-150 steps. Exhaustive coverage with repeated deep-dives into every subtopic.

## Key Planning Rules
1. CRITICAL: Plan enough steps to use at least 70% of the budget. Each step costs ~$0.005, so a $5 budget should have ~40+ planned steps, a $10 budget ~80+ steps.
2. Use research-lookup MULTIPLE TIMES with different queries — one per subtopic or research question angle.
3. Use literature-review on specific subtopics, not just the broad topic. Plan separate literature-review steps for each major theme.
4. Use data-visualization MULTIPLE TIMES — one per research question or figure type.
5. Use statistical-analysis MULTIPLE TIMES — one per analysis method or hypothesis.
6. Every skill execution builds material and citations for the final report — more executions = better paper.
7. The decision engine can extend the plan beyond what you specify here, but a comprehensive initial plan is critical for guiding research direction.
{quality_guidance}

## Instructions
Produce a YAML plan. Each step has: step number, skill name, and a short reason.
Only include skills that fit within the budget after reserving $0.03 for the report.
Plan should produce enough material for the report type matching this budget tier.

```yaml
domain: <one-line topic classification>
report_type: <Quick Summary | Literature Review | Research Report | Full Paper>
plan:
  - step: 1
    skill: <skill-name>
    reason: <why this step>
  - step: 2
    skill: <skill-name>
    reason: <why this step>
```"""
        text, usage = call_llm(prompt)
        # Validate YAML parsing inside exec so PocketFlow retries on failure
        parsed = parse_yaml_response(text)
        if not parsed or not isinstance(parsed.get("plan"), list) or len(parsed["plan"]) == 0:
            print(f"[BudgetPlanner] YAML parse failed or empty plan, retrying... Raw response:")
            print(text[:500])
            raise ValueError("BudgetPlanner: LLM returned invalid or empty YAML plan")
        return text, usage, parsed

    def post(self, shared, prep_res, exec_res):
        text, usage, parsed = exec_res
        track_cost(shared, "budget_planner", usage)

        shared["plan"] = parsed.get("plan", [])
        shared["domain"] = parsed.get("domain", "general")
        shared["report_type"] = parsed.get("report_type", "Literature Review")
        shared["budget_remaining"] = shared["budget_dollars"] - usage["cost"]
        shared["artifacts"] = {}
        shared["bibtex_entries"] = []
        shared["history"] = []
        shared["fix_attempts"] = 0

        # Create task directory early so all phases can persist intermediaries
        task_id = str(uuid.uuid4())
        out_dir = Path(shared.get("output_dir", "outputs")) / task_id
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "artifacts").mkdir(exist_ok=True)
        (out_dir / "figures").mkdir(exist_ok=True)
        (out_dir / "data").mkdir(exist_ok=True)
        (out_dir / "scripts").mkdir(exist_ok=True)
        shared["output_path"] = str(out_dir)

        # Persist plan
        import yaml as _yaml
        plan_data = {
            "task_id": task_id,
            "topic": shared["topic"],
            "domain": shared["domain"],
            "report_type": shared["report_type"],
            "budget_dollars": shared["budget_dollars"],
            "plan": shared["plan"],
        }
        (out_dir / "plan.yaml").write_text(
            _yaml.dump(plan_data, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )

        print(f"[BudgetPlanner] Task directory: {out_dir}")
        print(f"[BudgetPlanner] Domain: {shared['domain']}")
        print(f"[BudgetPlanner] Report type: {shared['report_type']}")
        print(f"[BudgetPlanner] Plan: {len(shared['plan'])} steps")
        for s in shared["plan"]:
            print(f"  {s['step']}. {s['skill']} — {s.get('reason', '')}")
        print(f"[BudgetPlanner] Budget remaining: ${shared['budget_remaining']:.4f}")
        return "execute"


# ===================================================================
# 2. DecideNext — the agent core
# ===================================================================
class DecideNext(Node):
    """Pick the next skill to execute or decide to write the report."""

    def prep(self, shared):
        # Build compact history summary
        history_lines = []
        for h in shared.get("history", []):
            history_lines.append(f"- {h['skill']}: {h['summary']} (${h['cost']:.4f})")
        history_text = "\n".join(history_lines) if history_lines else "None yet."

        # Remaining plan steps — count-based to preserve duplicate skills
        exec_counts = Counter(h["skill"] for h in shared.get("history", []))
        remaining = []
        skill_seen = Counter()
        for s in shared.get("plan", []):
            skill = s["skill"]
            skill_seen[skill] += 1
            if skill_seen[skill] > exec_counts.get(skill, 0):
                remaining.append(s)

        return {
            "topic": shared["topic"],
            "remaining_plan": remaining,
            "history": history_text,
            "budget_remaining": shared.get("budget_remaining", 0),
            "artifact_keys": list(shared.get("artifacts", {}).keys()),
            "available_skills": format_skill_index(shared["skill_index"]),
        }

    def exec(self, prep_res):
        # Force write_tex if budget is too low
        if prep_res["budget_remaining"] < BUDGET_RESERVE:
            return {"action": "write_tex", "reason": "budget exhausted"}, {"input_tokens": 0, "output_tokens": 0, "cost": 0}

        remaining_yaml = "\n".join(
            f"  - {s['skill']}: {s.get('reason', '')}"
            for s in prep_res["remaining_plan"]
        ) if prep_res["remaining_plan"] else "All planned steps completed."

        # Calculate how many more skill calls the budget can support
        cost_per_skill = 0.005  # conservative estimate
        usable_budget = prep_res["budget_remaining"] - BUDGET_RESERVE
        affordable_steps = max(0, int(usable_budget / cost_per_skill))

        prompt = f"""You are the decision engine of an autonomous research agent.

## Research Topic
{prep_res["topic"]}

## Completed Steps
{prep_res["history"]}

## Remaining Planned Steps
{remaining_yaml}

## Budget Remaining
${prep_res["budget_remaining"]:.4f} (reserve $0.03 for final report)
Estimated affordable additional skill calls: {affordable_steps}

## Artifacts Collected
{', '.join(prep_res["artifact_keys"]) if prep_res["artifact_keys"] else 'None yet.'}

## Available Skills (for extending the plan)
{prep_res["available_skills"]}

## Instructions
Decide the next action. You may:
1. Execute a planned skill ("execute_skill") — pick from remaining plan
2. Execute an ADDITIONAL skill ("execute_skill") — if the plan is done but budget allows deeper research, propose a skill to strengthen the paper (e.g., repeat research-lookup with different angles, add peer-review, add scientific-critical-thinking, deepen literature-review on subtopics, additional data-visualization or statistical-analysis passes)
3. Write the final report ("write_tex") — ONLY when BOTH conditions are met:
   a. You have comprehensive material (20+ citations for Full Paper, figures, tables, all RQs addressed)
   b. Less than 10 affordable steps remain OR budget utilization exceeds 70%

**CRITICAL**: You MUST continue executing skills if budget utilization is below 60%. Each additional skill call deepens the research quality. Propose NEW angles, deeper analysis, additional visualizations, or cross-validation steps. NEVER stop early with substantial budget remaining — the user is paying for thorough research.

Return YAML:
```yaml
action: execute_skill OR write_tex
skill: <skill-name>
reason: <brief reason>
```"""
        text, usage = call_llm(prompt)
        parsed = parse_yaml_response(text)
        return parsed, usage

    def post(self, shared, prep_res, exec_res):
        decision, usage = exec_res
        track_cost(shared, "decide_next", usage)

        if not decision or not isinstance(decision, dict):
            print("[DecideNext] WARNING: Failed to parse LLM response, defaulting to next plan step")
            remaining = prep_res.get("remaining_plan", [])
            if remaining:
                decision = {"action": "execute_skill", "skill": remaining[0]["skill"], "reason": "parse fallback"}
            else:
                decision = {"action": "write_tex", "reason": "parse fallback — no remaining steps"}

        action = decision.get("action", "write_tex")
        reason = decision.get("reason", "")
        print(f"[DecideNext] Action: {action} — {reason}")
        print(f"[DecideNext] Budget remaining: ${shared['budget_remaining']:.4f}")

        # Persist decision log to task directory
        shared.setdefault("decisions", []).append({
            "action": action,
            "skill": decision.get("skill", ""),
            "reason": reason,
            "budget_remaining": shared["budget_remaining"],
        })
        import json as _json
        out_dir = Path(shared["output_path"])
        (out_dir / "decisions.json").write_text(
            _json.dumps(shared["decisions"], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Budget guard — too low → write
        if shared["budget_remaining"] < BUDGET_RESERVE:
            print("[DecideNext] Budget guard triggered → write_tex")
            return "write_tex"

        # Budget utilization guard — override premature write_tex
        # If LLM wants to write but we've used less than 60% of total budget,
        # force another skill execution to deepen the research.
        if action == "write_tex":
            total = shared.get("budget_dollars", 1)
            used_frac = 1 - (shared["budget_remaining"] / total)
            min_utilization = 0.60
            if used_frac < min_utilization:
                # Pick a deepening skill — cycle through useful extensions
                deepen_cycle = [
                    "research-lookup", "literature-review",
                    "statistical-analysis", "data-visualization",
                    "scientific-critical-thinking", "peer-review",
                    "hypothesis-generation", "citation-management",
                ]
                # Pick one we've used least
                exec_counts = Counter(h["skill"] for h in shared.get("history", []))
                # Filter to skills that exist in the skill index
                valid = [s for s in deepen_cycle if s in shared.get("skill_index", {})]
                if valid:
                    best = min(valid, key=lambda s: exec_counts.get(s, 0))
                    print(f"[DecideNext] Budget utilization {used_frac:.0%} < {min_utilization:.0%} "
                          f"— overriding write_tex → execute_skill ({best})")
                    shared["next_skill"] = best
                    # Update decision log with override
                    shared["decisions"][-1]["action"] = "execute_skill"
                    shared["decisions"][-1]["skill"] = best
                    shared["decisions"][-1]["reason"] += f" [OVERRIDDEN: budget {used_frac:.0%} used]"
                    return "execute_skill"

        if action == "execute_skill":
            shared["next_skill"] = decision.get("skill", "")
            return "execute_skill"
        return "write_tex"


# ===================================================================
# 3. ExecuteSkill
# ===================================================================
class ExecuteSkill(Node):
    """Load a skill's SKILL.md, run it via LLM, and execute any code blocks."""

    def prep(self, shared):
        skill_name = shared["next_skill"]
        # Lazy-load: read the SKILL.md and parse metadata
        skill_content, skill_metadata = load_skill_content(shared["skills_dir"], skill_name)

        # Detect code execution capability
        allowed_tools = skill_metadata.get("allowed-tools", [])
        can_execute = "Bash" in allowed_tools

        # Find available scripts for this skill
        scripts_dir = Path(shared["skills_dir"]) / skill_name / "scripts"
        available_scripts = []
        if scripts_dir.is_dir():
            available_scripts = [f.name for f in scripts_dir.iterdir()
                                 if f.suffix == ".py" and f.is_file()]

        # Condensed prior context (summaries only, not full artifacts)
        context_lines = []
        for h in shared.get("history", []):
            context_lines.append(f"### {h['skill']}\n{h['summary']}")
        prior_context = "\n\n".join(context_lines) if context_lines else "No prior research yet."

        # Collect existing generated data files for context
        generated_files = shared.get("generated_files", {})
        data_files_info = ""
        if generated_files:
            file_lines = []
            for sk, files in generated_files.items():
                for f in files:
                    file_lines.append(f"  - {f} (from {sk})")
            if file_lines:
                data_files_info = "Previously generated data files:\n" + "\n".join(file_lines)

        return {
            "skill_name": skill_name,
            "skill_content": skill_content,
            "topic": shared["topic"],
            "prior_context": prior_context,
            "can_execute": can_execute,
            "available_scripts": available_scripts,
            "scripts_dir": str(scripts_dir) if scripts_dir.is_dir() else "",
            "task_dir": shared.get("output_path", ""),
            "data_files_info": data_files_info,
        }

    def exec(self, prep_res):
        # Build code execution instructions if the skill supports it
        code_exec_block = ""
        if prep_res["can_execute"] and prep_res["task_dir"]:
            scripts_info = ""
            if prep_res["available_scripts"]:
                scripts_info = f"""
### Available Skill Scripts (in {prep_res['scripts_dir']})
These scripts are ready to use. Call them with `python {prep_res['scripts_dir']}/<script_name>`:
{chr(10).join(f'- {s}' for s in prep_res['available_scripts'])}
"""
            data_info = ""
            if prep_res["data_files_info"]:
                data_info = f"""
### Previously Generated Data
{prep_res['data_files_info']}
You can read these files in your code for further analysis or visualization.
"""

            code_exec_block = f"""

## Code Execution Available
You can include executable code to collect REAL data, generate REAL figures, or run REAL analyses.
Your working directory is: {prep_res['task_dir']}
{scripts_info}{data_info}
### How to include executable code
Place code between these markers. Supported: python, bash.

%%BEGIN CODE:python%%
# Your Python code here
# Save data to: data/  (relative path)
# Save figures to: figures/  (relative path)
%%END CODE%%

%%BEGIN CODE:bash%%
# Your bash commands here (use relative paths)
%%END CODE%%

### Code Guidelines
- Your working directory is already set to the task directory. Use RELATIVE paths only.
- Save data files (CSV, JSON) to `data/` (relative path)
- Save figure files (PNG, PDF) to `figures/` (relative path)
- Do NOT use absolute paths or repeat the task directory path in your code.
- For figures: use matplotlib with `plt.savefig('figures/filename.png')` — do NOT use `plt.show()`
- Use descriptive filenames relating to the research topic
- Available libraries: matplotlib, pandas, numpy, seaborn, requests, scipy
- API tokens available as env vars: GITHUB_TOKEN, OPENROUTER_API_KEY, PERPLEXITY_API_KEY
- Timeout: 300 seconds — keep code focused and efficient
- Print a summary of collected/generated data to stdout
- IMPORTANT: You MUST include code blocks to produce real data and figures. Do NOT just describe what code would do — actually write it so it runs.
"""

        prompt = f"""You are executing a research skill as part of an autonomous scientist agent.

## Research Topic
{prep_res["topic"]}

## Prior Research Context
{prep_res["prior_context"]}

## Skill Instructions
Follow these instructions to produce your deliverable:

---
{prep_res["skill_content"]}
---
{code_exec_block}
## Citation Quality Requirements
- Include references to real, well-known papers in the field.
- Aim for breadth: cite multiple research groups, not just one lab.
- Include foundational/seminal papers and recent work (last 5 years when possible).
- Every claim or finding you mention should be backed by a citation.
- Target at least 5-10 references per skill execution.

## Output Format
1. Produce the skill's deliverable as detailed text.
2. At the VERY END of your response, you MUST include a BibTeX section between markers:

%%BEGIN BIBTEX%%
@article{{authorYYYYkeyword,
  author = {{Last, First and Last, First}},
  title = {{Full Paper Title}},
  journal = {{Journal Name}},
  year = {{YYYY}},
  volume = {{N}},
  pages = {{1--10}}
}}
%%END BIBTEX%%

3. Each BibTeX entry MUST have: author, title, year, and venue (journal or booktitle).
4. Use realistic cite keys: author2024keyword (e.g., smith2023attention).
5. Include one entry for EVERY paper you reference in your text.
6. This section is MANDATORY — do not skip it.

Begin your work now."""
        text, usage = call_llm(prompt)
        return text, usage

    def post(self, shared, prep_res, exec_res):
        text, usage = exec_res
        skill_name = prep_res["skill_name"]
        track_cost(shared, f"execute_skill:{skill_name}", usage)

        # --- Extract and execute code blocks ---
        code_outputs = []

        if prep_res["can_execute"] and prep_res["task_dir"]:
            task_dir = Path(prep_res["task_dir"]).resolve()

            # Ensure subdirs exist
            (task_dir / "data").mkdir(exist_ok=True)
            (task_dir / "figures").mkdir(exist_ok=True)
            (task_dir / "scripts").mkdir(exist_ok=True)

            # Extract code blocks: %%BEGIN CODE:lang%% ... %%END CODE%%
            code_blocks = re.findall(
                r"%%BEGIN CODE:(\w+)%%(.*?)%%END CODE%%", text, re.DOTALL
            )

            for i, (lang, code) in enumerate(code_blocks):
                code = code.strip()
                if not code:
                    continue

                # Write script to task_dir/scripts/
                step_num = len(shared.get("history", [])) + 1
                ext = ".py" if lang == "python" else ".sh"
                script_path = task_dir / "scripts" / f"{step_num:02d}_{skill_name}_{i:02d}{ext}"
                script_path.write_text(code, encoding="utf-8")

                # Execute
                cmd = ["python", str(script_path)] if lang == "python" else ["bash", str(script_path)]
                try:
                    result = subprocess.run(
                        cmd,
                        cwd=str(task_dir),
                        capture_output=True,
                        text=True,
                        errors="replace",
                        timeout=300,
                        env={**os.environ},
                    )
                    stdout = result.stdout[:3000]
                    stderr = result.stderr[:1000]
                    code_outputs.append(f"[Script {script_path.name}] exit={result.returncode}\n{stdout}")
                    if result.returncode != 0:
                        code_outputs.append(f"[STDERR] {stderr}")
                    print(f"[ExecuteSkill] Ran {script_path.name}: exit={result.returncode}")
                except subprocess.TimeoutExpired:
                    code_outputs.append(f"[Script {script_path.name}] TIMEOUT after 300s")
                    print(f"[ExecuteSkill] Script {script_path.name} timed out")
                except Exception as e:
                    code_outputs.append(f"[Script {script_path.name}] ERROR: {e}")
                    print(f"[ExecuteSkill] Script {script_path.name} failed: {e}")

            # Scan for generated files
            generated_files = []
            for subdir in ["data", "figures"]:
                scan_dir = task_dir / subdir
                if scan_dir.is_dir():
                    for f in sorted(scan_dir.iterdir()):
                        if f.is_file():
                            generated_files.append(str(f))
            if generated_files:
                shared.setdefault("generated_files", {})[skill_name] = generated_files
                for gf in generated_files:
                    print(f"[ExecuteSkill] Generated: {gf}")

        # --- Extract BibTeX ---
        bibtex_match = re.search(r"%%BEGIN BIBTEX%%(.*?)%%END BIBTEX%%", text, re.DOTALL)
        if bibtex_match:
            main_content = text[:bibtex_match.start()].strip()
            bib_block = bibtex_match.group(1).strip()
            bib_entries = re.findall(r"(@\w+\{[^@]+)", bib_block, re.DOTALL)
            bib_entries = [e.strip() for e in bib_entries if e.strip()]
        else:
            # Fallback: try fenced code blocks and raw entries
            main_content, bib_entries = extract_bibtex(text)

        # Remove code blocks from main content for cleaner artifact storage
        main_content = re.sub(
            r"%%BEGIN CODE:\w+%%.*?%%END CODE%%", "", main_content, flags=re.DOTALL
        ).strip()

        # Append code execution results to the artifact
        if code_outputs:
            main_content += "\n\n## Code Execution Results\n" + "\n".join(code_outputs)

        shared["artifacts"][skill_name] = main_content
        shared["bibtex_entries"].extend(bib_entries)

        # Create short summary for history (first 300 chars)
        summary = main_content[:300].replace("\n", " ")
        if len(main_content) > 300:
            summary += "..."
        step_num = len(shared["history"]) + 1
        shared["history"].append({
            "step": step_num,
            "skill": skill_name,
            "summary": summary,
            "cost": usage["cost"],
        })

        # Persist full artifact and BibTeX to task directory
        out_dir = Path(shared["output_path"])
        artifact_dir = out_dir / "artifacts"
        artifact_dir.mkdir(exist_ok=True)
        artifact_file = artifact_dir / f"{step_num:02d}_{skill_name}.md"
        artifact_file.write_text(main_content, encoding="utf-8")
        if bib_entries:
            bib_file = artifact_dir / f"{step_num:02d}_{skill_name}.bib"
            bib_file.write_text("\n\n".join(bib_entries) + "\n", encoding="utf-8")

        # Persist accumulated history snapshot
        import json as _json
        (out_dir / "history.json").write_text(
            _json.dumps(shared["history"], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print(f"[ExecuteSkill] Completed: {skill_name} (${usage['cost']:.4f})")
        print(f"[ExecuteSkill] Saved: {artifact_file.name}")
        print(f"[ExecuteSkill] BibTeX entries found: {len(bib_entries)}")
        print(f"[ExecuteSkill] Budget remaining: ${shared['budget_remaining']:.4f}")
        return "decide"


# ===================================================================
# 3b. GenerateFigures — runs after skills, before WriteTeX
# ===================================================================
class GenerateFigures(Node):
    """Generate figures and tables from collected artifacts and data."""

    def prep(self, shared):
        out_dir = Path(shared.get("output_path", "")).resolve()

        # Check if figures already exist
        figures_dir = out_dir / "figures"
        existing_figures = []
        if figures_dir.is_dir():
            existing_figures = [f.name for f in figures_dir.iterdir()
                                if f.is_file() and f.suffix.lower() in (".png", ".pdf", ".jpg", ".jpeg")]

        # Check for data files
        data_dir = out_dir / "data"
        data_files = []
        if data_dir.is_dir():
            data_files = [f.name for f in data_dir.iterdir() if f.is_file()]

        # Collect artifact summaries for context
        artifact_summaries = []
        for name, content in shared.get("artifacts", {}).items():
            artifact_summaries.append(f"### {name}\n{content[:500]}")

        return {
            "topic": shared["topic"],
            "out_dir": str(out_dir),
            "existing_figures": existing_figures,
            "data_files": data_files,
            "artifact_summaries": "\n\n".join(artifact_summaries),
            "report_type": shared.get("report_type", "Literature Review"),
            "budget_remaining": shared.get("budget_remaining", 0),
        }

    def exec(self, prep_res):
        # Skip if budget is very low or figures already exist
        if prep_res["budget_remaining"] < BUDGET_RESERVE * 2:
            return None, {"input_tokens": 0, "output_tokens": 0, "cost": 0}
        if len(prep_res["existing_figures"]) >= 3:
            return None, {"input_tokens": 0, "output_tokens": 0, "cost": 0}

        data_context = ""
        if prep_res["data_files"]:
            data_context = f"""
## Available Data Files (in data/ directory)
{chr(10).join(f'- {f}' for f in prep_res['data_files'])}

You can load these files with pandas to create data-driven figures:
```python
import pandas as pd
df = pd.read_csv('data/filename.csv')  # or json.load(open('data/filename.json'))
```
"""

        prompt = f"""You are a scientific visualization specialist. Generate Python code that creates publication-quality figures for a research paper.

## Research Topic
{prep_res["topic"]}

## Report Type: {prep_res["report_type"]}

## Research Content Summary
{prep_res["artifact_summaries"][:4000]}
{data_context}
## Requirements
You MUST generate exactly 3 Python scripts, each creating one figure. Output them as separate code blocks.

### Figure 1: Overview/Architecture diagram
Create a conceptual diagram, system overview, or taxonomy visualization using matplotlib.
Use boxes, arrows, and annotations to illustrate the key concepts or framework.

### Figure 2: Quantitative comparison or trends
Create a bar chart, line plot, or heatmap showing quantitative findings from the research.
If data files exist, load and visualize them. Otherwise, extract numerical data from the artifacts.

### Figure 3: Analysis/Distribution visualization
Create a visualization showing distributions, correlations, or multi-dimensional comparisons.
Options: grouped bar chart, radar/spider chart, scatter plot, violin plot, or bubble chart.

## Code Rules
- Each script is INDEPENDENT (include all imports in each)
- Use matplotlib and seaborn with a professional style: `plt.style.use('seaborn-v0_8-paper')` or `sns.set_theme(style='whitegrid')`
- Use a consistent color palette across all figures: `sns.color_palette('Set2')` or similar
- Set figure size to (10, 6) or (8, 6) for readability
- Use descriptive axis labels, titles, and legends with fontsize >= 12
- Save to `figures/` using RELATIVE paths only
- Use `plt.tight_layout()` before saving
- Use `plt.savefig('figures/filename.png', dpi=300, bbox_inches='tight')`
- Do NOT call `plt.show()`
- Print a description of what the figure shows to stdout

## Output Format
Return exactly 3 code blocks:

%%BEGIN CODE:python%%
# Figure 1: <description>
<complete Python script>
%%END CODE%%

%%BEGIN CODE:python%%
# Figure 2: <description>
<complete Python script>
%%END CODE%%

%%BEGIN CODE:python%%
# Figure 3: <description>
<complete Python script>
%%END CODE%%

Generate the code now. Every script MUST produce a .png file in figures/."""
        text, usage = call_llm(prompt)
        return text, usage

    def post(self, shared, prep_res, exec_res):
        text, usage = exec_res

        if text is None:
            print("[GenerateFigures] Skipped (budget low or figures exist)")
            return "write"

        track_cost(shared, "generate_figures", usage)

        out_dir = Path(prep_res["out_dir"])
        (out_dir / "figures").mkdir(exist_ok=True)
        (out_dir / "scripts").mkdir(exist_ok=True)

        # Extract and execute code blocks
        code_blocks = re.findall(
            r"%%BEGIN CODE:(\w+)%%(.*?)%%END CODE%%", text, re.DOTALL
        )

        figures_generated = 0
        for i, (lang, code) in enumerate(code_blocks):
            code = code.strip()
            if not code:
                continue

            script_path = out_dir / "scripts" / f"fig_{i:02d}.py"
            script_path.write_text(code, encoding="utf-8")

            cmd = ["python", str(script_path)]
            try:
                result = subprocess.run(
                    cmd,
                    cwd=str(out_dir),
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=120,
                    env={**os.environ},
                )
                if result.returncode == 0:
                    print(f"[GenerateFigures] Script fig_{i:02d}.py succeeded")
                    figures_generated += 1
                else:
                    print(f"[GenerateFigures] Script fig_{i:02d}.py failed (exit={result.returncode}): {result.stderr[:200]}")
            except subprocess.TimeoutExpired:
                print(f"[GenerateFigures] Script fig_{i:02d}.py timed out")
            except Exception as e:
                print(f"[GenerateFigures] Script fig_{i:02d}.py error: {e}")

        # Scan for generated figures
        figures_dir = out_dir / "figures"
        all_figures = []
        if figures_dir.is_dir():
            all_figures = [f.name for f in sorted(figures_dir.iterdir())
                          if f.is_file() and f.suffix.lower() in (".png", ".pdf", ".jpg", ".jpeg")]

        print(f"[GenerateFigures] Generated {figures_generated} scripts, {len(all_figures)} figure files in figures/")
        print(f"[GenerateFigures] Budget remaining: ${shared['budget_remaining']:.4f}")
        return "write"


# ===================================================================
# 3c. GenerateTables — produce LaTeX tables from artifacts/data
# ===================================================================
class GenerateTables(Node):
    """Generate LaTeX tables summarizing research findings."""

    def prep(self, shared):
        out_dir = Path(shared.get("output_path", "")).resolve()

        # Collect data files for context
        data_dir = out_dir / "data"
        data_files = []
        data_previews = {}
        if data_dir.is_dir():
            for f in sorted(data_dir.iterdir()):
                if f.is_file() and f.suffix.lower() in (".csv", ".json", ".tsv"):
                    data_files.append(f.name)
                    # Read first 2KB of each data file for context
                    try:
                        preview = f.read_text(encoding="utf-8", errors="replace")[:2000]
                        data_previews[f.name] = preview
                    except Exception:
                        pass

        # Collect artifact summaries
        artifact_summaries = []
        for name, content in shared.get("artifacts", {}).items():
            artifact_summaries.append(f"### {name}\n{content[:600]}")

        return {
            "topic": shared["topic"],
            "out_dir": str(out_dir),
            "data_files": data_files,
            "data_previews": data_previews,
            "artifact_summaries": "\n\n".join(artifact_summaries),
            "report_type": shared.get("report_type", "Literature Review"),
            "budget_remaining": shared.get("budget_remaining", 0),
        }

    def exec(self, prep_res):
        # Skip if budget is very low
        if prep_res["budget_remaining"] < BUDGET_RESERVE * 2:
            return None, {"input_tokens": 0, "output_tokens": 0, "cost": 0}

        data_context = ""
        if prep_res["data_previews"]:
            previews = []
            for fname, preview in prep_res["data_previews"].items():
                previews.append(f"### {fname}\n```\n{preview[:1000]}\n```")
            data_context = f"""
## Available Data Files (with previews)
{chr(10).join(previews)}
"""

        prompt = f"""You are a scientific table specialist. Generate LaTeX tables that summarize research findings for a paper.

## Research Topic
{prep_res["topic"]}

## Report Type: {prep_res["report_type"]}

## Research Artifacts
{prep_res["artifact_summaries"][:4000]}
{data_context}
## Requirements
Generate 2-3 LaTeX tables that present key findings. Tables should use the `booktabs` package.

### Table types to consider:
1. **Comparison table**: Compare methods, tools, frameworks, or approaches across multiple dimensions
2. **Results/Statistics table**: Quantitative metrics, counts, percentages from the research
3. **Summary/Taxonomy table**: Categorize key concepts, features, or findings

## LaTeX Table Rules
- Use `booktabs` style: `\\toprule`, `\\midrule`, `\\bottomrule` (no vertical lines)
- Include `\\caption{{...}}` and `\\label{{tab:...}}`
- Wrap in `\\begin{{table}}[htbp]` environment
- Keep tables readable: max 6-7 columns
- Use `\\centering` inside the table environment
- Use real data from the artifacts — do NOT fabricate numbers

## Output Format
Return each table between markers:

%%BEGIN TABLE%%
\\begin{{table}}[htbp]
\\centering
\\caption{{Descriptive caption here.}}
\\label{{tab:label-here}}
\\begin{{tabular}}{{lcc}}
\\toprule
Header 1 & Header 2 & Header 3 \\\\
\\midrule
Data & Data & Data \\\\
\\bottomrule
\\end{{tabular}}
\\end{{table}}
%%END TABLE%%

Generate the tables now. Each table MUST be wrapped in %%BEGIN TABLE%% / %%END TABLE%% markers."""
        text, usage = call_llm(prompt)
        return text, usage

    def post(self, shared, prep_res, exec_res):
        text, usage = exec_res

        if text is None:
            print("[GenerateTables] Skipped (budget low)")
            return "write"

        track_cost(shared, "generate_tables", usage)

        # Extract tables from markers
        tables = re.findall(r"%%BEGIN TABLE%%(.*?)%%END TABLE%%", text, re.DOTALL)
        tables = [t.strip() for t in tables if t.strip()]

        if not tables:
            # Fallback: try to extract \begin{table} blocks directly
            tables = re.findall(
                r"(\\begin\{table\}.*?\\end\{table\})", text, re.DOTALL
            )
            tables = [t.strip() for t in tables if t.strip()]

        shared["latex_tables"] = tables
        print(f"[GenerateTables] Generated {len(tables)} LaTeX tables")
        print(f"[GenerateTables] Budget remaining: ${shared['budget_remaining']:.4f}")
        return "write"


# ===================================================================
# 4. WriteTeX
# ===================================================================
class WriteTeX(Node):
    """Synthesize all artifacts into compilable .tex + .bib files."""

    def prep(self, shared):
        # Collect all cite keys for the LLM to reference
        cite_keys = []
        for entry in shared.get("bibtex_entries", []):
            m = re.match(r"@\w+\{([^,]+),", entry)
            if m:
                cite_keys.append(m.group(1).strip())

        # Determine which sections have content based on report type
        report_type = shared.get("report_type", "Literature Review")
        has_methods = any(
            k in shared.get("artifacts", {})
            for k in ("statistical-analysis", "method-implementation", "experimental-evaluation")
        )
        has_results = has_methods  # results typically accompany methods

        # Extract writing guidelines from quality standard
        writing_guide = ""
        qs = shared.get("quality_standard", "")
        if qs:
            # Pull sections 2.1, 3, and 6 (structure, writing rules, checklist)
            section_req = re.search(
                r"### 2\.1 Mandatory Sections.*?(?=\n### 2\.2 |\Z)",
                qs, re.DOTALL,
            )
            writing_rules = re.search(
                r"## 3\. Writing Quality Rules.*?(?=\n## 4\.|\Z)",
                qs, re.DOTALL,
            )
            checklist = re.search(
                r"## 6\. Self-Assessment Checklist.*?(?=\n## Sources|\n---|\Z)",
                qs, re.DOTALL,
            )
            parts = []
            if section_req:
                parts.append(section_req.group(0).strip())
            if writing_rules:
                parts.append(writing_rules.group(0).strip())
            if checklist:
                parts.append(checklist.group(0).strip())
            if parts:
                writing_guide = "\n\n".join(parts)

        # Scan for generated figures
        figure_files = []
        out_dir = Path(shared.get("output_path", ""))
        figures_dir = out_dir / "figures"
        if figures_dir.is_dir():
            for f in sorted(figures_dir.iterdir()):
                if f.is_file() and f.suffix.lower() in (".png", ".pdf", ".jpg", ".jpeg"):
                    figure_files.append(f.name)

        # Scan for generated data files (for methods/results context)
        data_files = []
        data_dir = out_dir / "data"
        if data_dir.is_dir():
            for f in sorted(data_dir.iterdir()):
                if f.is_file():
                    data_files.append(f.name)

        return {
            "topic": shared["topic"],
            "artifacts": shared.get("artifacts", {}),
            "cite_keys": cite_keys,
            "has_methods": has_methods or bool(data_files),
            "has_results": has_results or bool(data_files),
            "report_type": report_type,
            "writing_guide": writing_guide,
            "figure_files": figure_files,
            "data_files": data_files,
            "latex_tables": shared.get("latex_tables", []),
        }

    def exec(self, prep_res):
        # Build context from artifacts
        artifact_text = ""
        for name, content in prep_res["artifacts"].items():
            artifact_text += f"\n\n### Artifact: {name}\n{content}"

        # Determine sections based on report type
        report_type = prep_res["report_type"]
        sections = ["abstract", "introduction", "background"]
        if report_type in ("Research Report", "Full Paper") and prep_res["has_methods"]:
            sections.append("methods")
        if report_type in ("Research Report", "Full Paper") and prep_res["has_results"]:
            sections.append("results")
        sections.extend(["discussion", "conclusion"])
        if report_type == "Full Paper":
            sections.append("limitations")

        cite_list = ", ".join(prep_res["cite_keys"]) if prep_res["cite_keys"] else "No citations available."

        # Build quality guidance block
        quality_block = ""
        if prep_res["writing_guide"]:
            quality_block = f"""
## Paper Quality Standard
You MUST follow these quality standards when writing. This is non-negotiable.

{prep_res["writing_guide"]}
"""

        # Build figure inclusion block
        figure_block = ""
        if prep_res.get("figure_files"):
            figure_list = "\n".join(f"- {f}" for f in prep_res["figure_files"])
            figure_block = f"""
## Available Figures
The following figures have been generated during research and are available for inclusion.
You MUST include them in the paper where they support the narrative.
{figure_list}

To include a figure, use this LaTeX pattern:
\\begin{{figure}}[htbp]
\\centering
\\includegraphics[width=0.8\\textwidth]{{figures/<filename>}}
\\caption{{Your descriptive caption here.}}
\\label{{fig:<short-label>}}
\\end{{figure}}

Reference each figure in the text as Figure~\\ref{{fig:<label>}}.
"""

        # Build data files context
        data_block = ""
        if prep_res.get("data_files"):
            data_list = "\n".join(f"- {f}" for f in prep_res["data_files"])
            data_block = f"""
## Collected Data Files
The following data files were collected during research. Reference their contents in your Methods and Results sections:
{data_list}
"""

        # Build pre-generated tables block
        tables_block = ""
        if prep_res.get("latex_tables"):
            tables_joined = "\n\n".join(prep_res["latex_tables"])
            tables_block = f"""
## Pre-Generated Tables
The following LaTeX tables have been generated from the research data. You MUST include them
in appropriate sections of the paper (typically Results or Background). Reference each table
in the text as Table~\\ref{{tab:label}}.

{tables_joined}
"""

        prompt = f"""You are writing a scientific {report_type.lower()} as compilable LaTeX.
{quality_block}
## Research Topic
{prep_res["topic"]}

## Research Artifacts (your source material)
{artifact_text}

## Available BibTeX cite keys
{cite_list}
{figure_block}{data_block}{tables_block}
## Report Type: {report_type}
## Sections to Write: {', '.join(sections)}

## STRUCTURAL RULES
1. **Title**: Specific, descriptive, under 15 words. Captures the central contribution.
2. **Abstract**: 150-250 words, self-contained, follows Context-Content-Conclusion structure. States problem, approach, findings, significance.
3. **Introduction**: Progress from broad context to specific gap. End with clear contribution statement.
4. **Background/Related Work**: Synthesize prior work by theme, not just list papers. Identify what is missing.
5. **Discussion**: Interpret results, compare with prior work, acknowledge limitations honestly.
6. **Conclusion**: 1-2 paragraphs max. State how work advances the field. Do NOT repeat the abstract.

## WRITING RULES
- Every paragraph follows Context-Content-Conclusion: first sentence sets context, body presents content, last sentence gives takeaway.
- Active voice preferred: "We propose X" not "X is proposed."
- No unsupported claims — every assertion needs \\cite{{key}} or evidence.
- Formal academic tone. No colloquialisms, contractions, or casual phrasing.
- Technical terms defined on first use.

## LATEX RULES
1. Write ONLY the LaTeX body content for each section.
2. Use \\cite{{key}} for citations (only keys listed above).
3. Do NOT include \\documentclass, \\usepackage, \\begin{{document}}, or \\end{{document}}.
4. Do NOT use any custom commands or undefined macros.
5. Only use standard LaTeX commands: \\section, \\subsection, \\textbf, \\textit, \\cite, \\ref, itemize, enumerate, equation, table, tabular, figure environments.
6. Write in full academic prose paragraphs, not bullet points.
7. Escape special characters: use \\% for percent, \\& for ampersand, etc.

## Output Format
Return your content between markers like this:

%%BEGIN TITLE%%
<the paper title — specific, under 15 words>
%%END TITLE%%

%%BEGIN ABSTRACT%%
<abstract text — 150-250 words, C-C-C structure, no \\begin{{abstract}} tags>
%%END ABSTRACT%%

%%BEGIN BODY%%
\\section{{Introduction}}
<introduction: broad context → specific gap → contribution statement>

\\section{{Background}}
<background: synthesize prior work by theme, cite extensively>

... (include only sections listed above)

\\section{{Conclusion}}
<conclusion: concise synthesis, how this advances the field>
%%END BODY%%

%%BEGIN BIBTEX%%
@article{{citekey1,
  author = {{Last, First}},
  title = {{Paper Title}},
  journal = {{Journal Name}},
  year = {{2024}},
  volume = {{1}},
  pages = {{1--10}}
}}
... (one BibTeX entry for EVERY \\cite{{key}} used in the body)
%%END BIBTEX%%

## CRITICAL: BibTeX Requirements
- You MUST include a %%BEGIN BIBTEX%% ... %%END BIBTEX%% section.
- Every \\cite{{key}} in the body MUST have a matching @article/@inproceedings entry in the BIBTEX section.
- Use realistic metadata: real author names, real paper titles, real venues, accurate years.
- Cite keys must match exactly between \\cite{{}} and @type{{key, ...}}.

Write the report now."""
        text, usage = call_llm(prompt)
        return text, usage

    def post(self, shared, prep_res, exec_res):
        text, usage = exec_res
        track_cost(shared, "write_tex", usage)

        # Extract sections from markers
        title_match = re.search(r"%%BEGIN TITLE%%(.*?)%%END TITLE%%", text, re.DOTALL)
        abstract_match = re.search(r"%%BEGIN ABSTRACT%%(.*?)%%END ABSTRACT%%", text, re.DOTALL)
        body_match = re.search(r"%%BEGIN BODY%%(.*?)%%END BODY%%", text, re.DOTALL)
        bibtex_match = re.search(r"%%BEGIN BIBTEX%%(.*?)%%END BIBTEX%%", text, re.DOTALL)

        title = title_match.group(1).strip() if title_match else prep_res["topic"]
        abstract = abstract_match.group(1).strip() if abstract_match else "Abstract not available."
        body = body_match.group(1).strip() if body_match else text.strip()

        # Extract BibTeX entries from WriteTeX output and merge with skill-collected entries
        if bibtex_match:
            bib_block = bibtex_match.group(1).strip()
            tex_bib_entries = re.findall(r"(@\w+\{[^@]+)", bib_block, re.DOTALL)
            tex_bib_entries = [e.strip() for e in tex_bib_entries if e.strip()]
            shared.setdefault("bibtex_entries", []).extend(tex_bib_entries)
            print(f"[WriteTeX] Extracted {len(tex_bib_entries)} BibTeX entries from report")

        # Assemble .tex from skeleton
        tex = LATEX_SKELETON
        tex = tex.replace("%% TITLE %%", title)
        tex = tex.replace("%% ABSTRACT %%", abstract)
        tex = tex.replace("%% BODY %%", body)

        # Deduplicate and write .bib
        bib_content = dedup_bibtex(shared.get("bibtex_entries", []))

        # Use existing task directory (created by BudgetPlanner)
        out_dir = Path(shared["output_path"])

        (out_dir / "report.tex").write_text(tex, encoding="utf-8")
        (out_dir / "references.bib").write_text(bib_content, encoding="utf-8")

        shared["tex_content"] = tex
        shared["bib_content"] = bib_content

        # --- Citation validation ---
        cite_keys_in_tex = set(re.findall(r"\\cite\{([^}]+)\}", body))
        # Expand comma-separated keys like \cite{a,b,c}
        all_cite_keys = set()
        for group in cite_keys_in_tex:
            for key in group.split(","):
                all_cite_keys.add(key.strip())

        bib_keys = set()
        for entry in shared.get("bibtex_entries", []):
            m = re.match(r"@\w+\{([^,]+),", entry)
            if m:
                bib_keys.add(m.group(1).strip())

        missing = all_cite_keys - bib_keys
        if not bib_content.strip():
            print(f"[WriteTeX] WARNING: references.bib is EMPTY — all citations will show as [?]")
        elif missing:
            print(f"[WriteTeX] WARNING: {len(missing)} cite keys missing from .bib: {', '.join(sorted(missing)[:10])}")
        else:
            print(f"[WriteTeX] Citation check passed: {len(all_cite_keys)} keys, all resolved")

        print(f"[WriteTeX] Wrote report.tex + references.bib to {out_dir}")
        print(f"[WriteTeX] Budget remaining: ${shared['budget_remaining']:.4f}")
        return "compile"


# ===================================================================
# 5. CompileTeX
# ===================================================================
class CompileTeX(Node):
    """Compile .tex + .bib → .pdf using pdflatex + bibtex."""

    def prep(self, shared):
        return shared["output_path"]

    def exec(self, out_dir):
        import shutil
        if not shutil.which("pdflatex"):
            return None, "pdflatex not found"

        cmds = [
            ["pdflatex", "-interaction=nonstopmode", "report.tex"],
            ["bibtex", "report"],
            ["pdflatex", "-interaction=nonstopmode", "report.tex"],
            ["pdflatex", "-interaction=nonstopmode", "report.tex"],
        ]
        all_output = []
        for cmd in cmds:
            result = subprocess.run(
                cmd,
                cwd=out_dir,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=60,
            )
            all_output.append(result.stdout + result.stderr)

        # Check if PDF was produced
        pdf_path = Path(out_dir) / "report.pdf"
        success = pdf_path.exists()
        return success, "\n".join(all_output)

    def post(self, shared, prep_res, exec_res):
        success, log = exec_res
        if success is None:
            print(f"[CompileTeX] pdflatex not installed — skipping PDF compilation.")
            print(f"[CompileTeX] LaTeX source ready at: {shared['output_path']}/report.tex")
            print(f"[CompileTeX] To compile manually: cd {shared['output_path']} && pdflatex report.tex && bibtex report && pdflatex report.tex && pdflatex report.tex")
            return "done"

        # Check for undefined citations even if PDF was produced
        undefined_cites = re.findall(r"Citation `([^']+)' on page", log)
        if undefined_cites:
            unique_missing = sorted(set(undefined_cites))
            print(f"[CompileTeX] WARNING: {len(unique_missing)} undefined citations: {', '.join(unique_missing[:10])}")
            shared["has_citation_warnings"] = True

        if success:
            if undefined_cites and shared.get("fix_attempts", 0) < 2:
                print(f"[CompileTeX] PDF has {len(unique_missing)} undefined citations, routing to fix...")
                shared["compile_errors"] = log
                shared["undefined_citations"] = unique_missing
                return "fix"
            elif undefined_cites:
                print(f"[CompileTeX] PDF compiled with citation warnings (fix attempts exhausted): {shared['output_path']}/report.pdf")
            else:
                print(f"[CompileTeX] PDF compiled successfully: {shared['output_path']}/report.pdf")
            return "done"
        else:
            shared["compile_errors"] = log
            print("[CompileTeX] Compilation failed, attempting fix...")
            return "fix"


# ===================================================================
# 6. FixTeX
# ===================================================================
class FixTeX(Node):
    """Fix LaTeX compilation errors or undefined citations."""

    def prep(self, shared):
        undefined_cites = shared.get("undefined_citations", [])
        return {
            "tex_content": shared["tex_content"],
            "bib_content": shared.get("bib_content", ""),
            "errors": shared.get("compile_errors", ""),
            "attempt": shared.get("fix_attempts", 0),
            "undefined_citations": undefined_cites,
            "mode": "citation" if undefined_cites else "latex_error",
        }

    def exec(self, prep_res):
        if prep_res["attempt"] >= 2:
            # Give up after 2 fix attempts
            return None, {"input_tokens": 0, "output_tokens": 0, "cost": 0}

        if prep_res["mode"] == "citation":
            # Citation fix mode: generate missing BibTeX entries
            missing_keys = prep_res["undefined_citations"]
            prompt = f"""The following BibTeX citation keys are used in a LaTeX document with \\cite{{key}} but are missing from the .bib file, causing [?] markers in the PDF.

## Missing Citation Keys
{', '.join(missing_keys)}

## Current .bib Content (for context, do NOT repeat existing entries)
{prep_res["bib_content"][:3000]}

## Instructions
1. For EACH missing key listed above, generate a plausible BibTeX entry.
2. Use the cite key EXACTLY as listed (do not rename it).
3. Use realistic metadata: real author names, real paper titles, real venues.
4. Each entry MUST have: author, title, year, and journal/booktitle.
5. Return ONLY the new BibTeX entries, nothing else. No explanation text.
6. Do not repeat entries already in the .bib file."""
            text, usage = call_llm(prompt)
            return text, usage
        else:
            # LaTeX error fix mode (original behavior)
            error_lines = []
            for line in prep_res["errors"].split("\n"):
                if line.startswith("!") or "Error" in line or "Undefined" in line:
                    error_lines.append(line)
            error_summary = "\n".join(error_lines[:30])

            prompt = f"""Fix these LaTeX compilation errors. Return the COMPLETE corrected .tex file content.

## Errors
{error_summary}

## Current .tex Content
{prep_res["tex_content"]}

## Rules
1. Do NOT change the \\documentclass or \\usepackage lines.
2. Only fix the errors in the body content.
3. Common fixes: escape special chars (%, &, #, $, _), close environments, fix undefined commands.
4. Return ONLY the complete .tex file, nothing else."""
            text, usage = call_llm(prompt)
            return text, usage

    def post(self, shared, prep_res, exec_res):
        text, usage = exec_res

        shared["fix_attempts"] = prep_res["attempt"] + 1

        if text is None:
            # Max attempts reached
            print(f"[FixTeX] Max fix attempts reached. Output may have compilation warnings.")
            return "done"

        track_cost(shared, f"fix_tex:{shared['fix_attempts']}", usage)

        # Clean up: strip markdown fences if the LLM wrapped it
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```\w*\n?", "", cleaned)
            cleaned = re.sub(r"\n?```$", "", cleaned)

        if prep_res["mode"] == "citation":
            # Citation fix: parse new entries, validate, and update .bib
            new_entries = re.findall(r"(@\w+\{[^@]+)", cleaned, re.DOTALL)
            new_entries = [e.strip() for e in new_entries if e.strip()]

            if new_entries:
                all_entries = shared.get("bibtex_entries", []) + new_entries
                combined = dedup_bibtex(all_entries)

                out_dir = Path(shared["output_path"])
                (out_dir / "references.bib").write_text(combined, encoding="utf-8")
                shared["bib_content"] = combined
                shared["bibtex_entries"] = all_entries

                print(f"[FixTeX] Added {len(new_entries)} BibTeX entries for undefined citations")

            # Clear flag so CompileTeX re-evaluates from scratch
            shared.pop("undefined_citations", None)
            print(f"[FixTeX] Citation fix applied (attempt {shared['fix_attempts']})")
            return "compile"
        else:
            # LaTeX error fix: rewrite .tex
            out_dir = Path(shared["output_path"])
            (out_dir / "report.tex").write_text(cleaned, encoding="utf-8")
            shared["tex_content"] = cleaned

            print(f"[FixTeX] Applied fix (attempt {shared['fix_attempts']})")
            return "compile"


# ===================================================================
# 7. QualityReview — check paper against quality standard before finishing
# ===================================================================
class QualityReview(Node):
    """When the paper is compiled but budget remains, review against
    PAPER_QUALITY_STANDARD.md and loop back to fill gaps."""

    MAX_REVIEW_ROUNDS = 3  # prevent infinite loops

    def prep(self, shared):
        total_budget = shared.get("budget_dollars", 1)
        remaining = shared.get("budget_remaining", 0)
        used_frac = 1 - (remaining / total_budget) if total_budget > 0 else 1.0
        rounds = shared.get("quality_review_rounds", 0)
        return {
            "used_frac": used_frac,
            "remaining": remaining,
            "rounds": rounds,
            "topic": shared.get("topic", ""),
            "report_type": shared.get("report_type", "Full Paper"),
            "history": shared.get("history", []),
            "artifacts": shared.get("artifacts", {}),
            "bibtex_count": len(shared.get("bibtex_entries", [])),
            "quality_standard": shared.get("quality_standard", ""),
            "skill_index": shared.get("skill_index", {}),
        }

    def exec(self, prep_res):
        # Skip review if budget mostly used, too little remaining, or max rounds hit
        if prep_res["used_frac"] >= 0.60:
            print(f"[QualityReview] Budget {prep_res['used_frac']:.0%} used — sufficient, skipping review")
            return None
        if prep_res["remaining"] < BUDGET_RESERVE * 3:
            print(f"[QualityReview] Only ${prep_res['remaining']:.3f} left — not enough to deepen")
            return None
        if prep_res["rounds"] >= self.MAX_REVIEW_ROUNDS:
            print(f"[QualityReview] Max review rounds ({self.MAX_REVIEW_ROUNDS}) reached — finishing")
            return None

        # Extract the self-assessment checklist (Section 10) from quality standard
        qs = prep_res["quality_standard"]
        checklist_section = ""
        if "## 10. Self-Assessment Checklist" in qs:
            checklist_section = qs[qs.index("## 10. Self-Assessment Checklist"):]
        else:
            checklist_section = qs[-2000:]  # fallback: last portion

        # Build summary of what we have
        skill_counts = Counter(h["skill"] for h in prep_res["history"])
        skills_summary = ", ".join(f"{s}({c})" for s, c in skill_counts.most_common())
        artifact_keys = list(prep_res["artifacts"].keys())

        prompt = f"""You are a research quality reviewer. A paper has been drafted on the following topic:

## Topic
{prep_res["topic"][:500]}

## Report Type
{prep_res["report_type"]}

## What Has Been Done
- Skills executed: {skills_summary}
- Total skill executions: {len(prep_res["history"])}
- Citations collected: {prep_res["bibtex_count"]}
- Artifacts/data collected: {len(artifact_keys)} items

## Paper Quality Checklist
{checklist_section}

## Available Skills for Deepening
{', '.join(prep_res["skill_index"].keys())}

## Budget Status
- Budget used: {prep_res["used_frac"]:.0%}
- Remaining: ${prep_res["remaining"]:.2f}
- Each additional skill call costs ~$0.005

## Instructions
Review the work done against the quality checklist. Identify the TOP 5 most critical gaps that would improve the paper. For each gap, suggest a specific skill call to address it.

Return YAML:
```yaml
gaps:
  - gap: <what is missing>
    skill: <skill-name to address it>
    query: <specific query or focus for the skill>
  - gap: <what is missing>
    skill: <skill-name>
    query: <specific focus>
verdict: deepen  # or "done" if the paper already meets the standard well
```"""
        text, usage = call_llm(prompt)
        return text, usage

    def post(self, shared, prep_res, exec_res):
        if exec_res is None:
            return "done"

        text, usage = exec_res
        track_cost(shared, "quality_review", usage)

        parsed = parse_yaml_response(text) or {}
        verdict = parsed.get("verdict", "done")
        gaps = parsed.get("gaps", [])

        shared["quality_review_rounds"] = prep_res["rounds"] + 1

        if verdict == "deepen" and gaps:
            # Convert gaps into new plan steps appended to the existing plan
            new_steps = []
            start_step = len(shared.get("plan", [])) + 1
            for i, gap in enumerate(gaps):
                if isinstance(gap, dict) and gap.get("skill") in prep_res["skill_index"]:
                    new_steps.append({
                        "step": start_step + i,
                        "skill": gap["skill"],
                        "reason": f"[QualityReview] {gap.get('gap', 'fill gap')}",
                        "query": gap.get("query", ""),
                    })

            if new_steps:
                shared["plan"].extend(new_steps)
                # Reset the write_tex completion so DecideNext sees remaining steps
                print(f"[QualityReview] Found {len(new_steps)} gaps — adding steps and looping back")
                for step in new_steps:
                    print(f"  → {step['skill']}: {step['reason']}")
                return "deepen"

        print("[QualityReview] Paper quality sufficient — proceeding to finish")
        return "done"


# ===================================================================
# 8. Finisher — terminal node (no successors → flow ends cleanly)
# ===================================================================
class Finisher(Node):
    """Print cost summary and end the flow."""

    def prep(self, shared):
        return shared

    def exec(self, prep_res):
        return None

    def post(self, shared, prep_res, exec_res):
        import json as _json
        from datetime import datetime, timezone

        total = sum(entry["cost"] for entry in shared.get("cost_log", []))
        out_dir = Path(shared["output_path"])

        # Persist cost log
        (out_dir / "cost_log.json").write_text(
            _json.dumps(shared.get("cost_log", []), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        # Persist final summary for post-analysis
        summary = {
            "topic": shared.get("topic", ""),
            "domain": shared.get("domain", ""),
            "report_type": shared.get("report_type", ""),
            "budget_dollars": shared.get("budget_dollars", 0),
            "total_cost": round(total, 6),
            "budget_remaining": round(shared.get("budget_remaining", 0), 6),
            "steps_executed": len(shared.get("history", [])),
            "artifacts": list(shared.get("artifacts", {}).keys()),
            "bibtex_count": len(shared.get("bibtex_entries", [])),
            "fix_attempts": shared.get("fix_attempts", 0),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        (out_dir / "summary.json").write_text(
            _json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print(f"\n{'='*50}")
        print(f"Research complete!")
        print(f"Total cost: ${total:.4f}")
        print(f"Budget used: ${total:.4f} / ${shared['budget_dollars']:.2f}")
        print(f"Output: {shared['output_path']}/")
        print(f"{'='*50}")
