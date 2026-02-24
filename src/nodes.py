"""PocketFlow nodes for the Autonomous Scientist agent."""

import re
import subprocess
import uuid
from pathlib import Path

from pocketflow import Node

from .utils import (
    call_llm,
    format_skill_index,
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
Each skill execution costs roughly $0.01 (15K input + 5K output tokens).
Each planning/decision step costs roughly $0.002.
The final LaTeX report generation costs roughly $0.015.
Always reserve $0.03 for the final report + compilation.

## Available Skills
{prep_res["skills"]}

## Budget Strategy Guidelines
- Budget < $0.10: Only use research-lookup for a quick search, then write report (Quick Summary: Title, Abstract, Intro, Background, Conclusion, References).
- Budget $0.10-$0.50: literature-review + write report (Literature Review: all sections except Methods/Results).
- Budget $0.50-$2.00: literature-review + hypothesis-generation + scientific-critical-thinking (Research Report: all mandatory sections).
- Budget $2.00-$5.00: Add statistical-analysis, scholar-evaluation, peer-review (Full Paper with figures, limitations, appendix).
- Budget $5.00+: Full pipeline with multiple iterations and venue formatting.
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
        return text, usage

    def post(self, shared, prep_res, exec_res):
        text, usage = exec_res
        track_cost(shared, "budget_planner", usage)

        parsed = parse_yaml_response(text)
        shared["plan"] = parsed.get("plan", [])
        shared["domain"] = parsed.get("domain", "general")
        shared["report_type"] = parsed.get("report_type", "Literature Review")
        shared["budget_remaining"] = shared["budget_dollars"] - usage["cost"]
        shared["artifacts"] = {}
        shared["bibtex_entries"] = []
        shared["history"] = []
        shared["fix_attempts"] = 0

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

        # Remaining plan steps
        completed_skills = {h["skill"] for h in shared.get("history", [])}
        remaining = [
            s for s in shared.get("plan", []) if s["skill"] not in completed_skills
        ]

        return {
            "topic": shared["topic"],
            "remaining_plan": remaining,
            "history": history_text,
            "budget_remaining": shared.get("budget_remaining", 0),
            "artifact_keys": list(shared.get("artifacts", {}).keys()),
        }

    def exec(self, prep_res):
        # Force write_tex if budget is too low
        if prep_res["budget_remaining"] < BUDGET_RESERVE:
            return {"action": "write_tex", "reason": "budget exhausted"}, {"input_tokens": 0, "output_tokens": 0, "cost": 0}

        # Force write_tex if no remaining plan steps
        if not prep_res["remaining_plan"]:
            return {"action": "write_tex", "reason": "plan complete"}, {"input_tokens": 0, "output_tokens": 0, "cost": 0}

        remaining_yaml = "\n".join(
            f"  - {s['skill']}: {s.get('reason', '')}"
            for s in prep_res["remaining_plan"]
        )

        prompt = f"""You are the decision engine of an autonomous research agent.

## Research Topic
{prep_res["topic"]}

## Completed Steps
{prep_res["history"]}

## Remaining Planned Steps
{remaining_yaml}

## Budget Remaining
${prep_res["budget_remaining"]:.4f} (reserve $0.03 for final report)

## Artifacts Collected
{', '.join(prep_res["artifact_keys"]) if prep_res["artifact_keys"] else 'None yet.'}

## Instructions
Decide the next action. You may:
1. Execute the next planned skill ("execute_skill")
2. Skip to writing the final report ("write_tex") if you have enough material or budget is low

Return YAML:
```yaml
action: execute_skill OR write_tex
skill: <skill-name if execute_skill, omit if write_tex>
reason: <brief reason>
```"""
        text, usage = call_llm(prompt)
        parsed = parse_yaml_response(text)
        return parsed, usage

    def post(self, shared, prep_res, exec_res):
        decision, usage = exec_res
        track_cost(shared, "decide_next", usage)

        action = decision.get("action", "write_tex")
        reason = decision.get("reason", "")
        print(f"[DecideNext] Action: {action} — {reason}")
        print(f"[DecideNext] Budget remaining: ${shared['budget_remaining']:.4f}")

        # Budget guard
        if shared["budget_remaining"] < BUDGET_RESERVE:
            print("[DecideNext] Budget guard triggered → write_tex")
            return "write_tex"

        if action == "execute_skill":
            shared["next_skill"] = decision.get("skill", "")
            return "execute_skill"
        return "write_tex"


# ===================================================================
# 3. ExecuteSkill
# ===================================================================
class ExecuteSkill(Node):
    """Load a skill's SKILL.md and run it via LLM."""

    def prep(self, shared):
        skill_name = shared["next_skill"]
        # Lazy-load: only read the SKILL.md we actually need right now
        skill_content = load_skill_content(shared["skills_dir"], skill_name)

        # Condensed prior context (summaries only, not full artifacts)
        context_lines = []
        for h in shared.get("history", []):
            context_lines.append(f"### {h['skill']}\n{h['summary']}")
        prior_context = "\n\n".join(context_lines) if context_lines else "No prior research yet."

        return {
            "skill_name": skill_name,
            "skill_content": skill_content,
            "topic": shared["topic"],
            "prior_context": prior_context,
        }

    def exec(self, prep_res):
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

## Citation Quality Requirements
- Include references to real, well-known papers in the field.
- Aim for breadth: cite multiple research groups, not just one lab.
- Include foundational/seminal papers and recent work (last 5 years when possible).
- Every claim or finding you mention should be backed by a citation.

## Output Requirements
1. Produce the skill's deliverable as detailed text.
2. At the END of your response, include a ```bibtex block with BibTeX entries for ALL papers you reference.
3. Each BibTeX entry MUST have: author, title, year, and venue (journal or booktitle).
4. Use realistic cite keys following the pattern: author2024keyword (e.g., smith2023attention).
5. If no references, omit the bibtex block.

Begin your work now."""
        text, usage = call_llm(prompt)
        return text, usage

    def post(self, shared, prep_res, exec_res):
        text, usage = exec_res
        skill_name = prep_res["skill_name"]
        track_cost(shared, f"execute_skill:{skill_name}", usage)

        # Split main content from bibtex
        main_content, bib_entries = extract_bibtex(text)
        shared["artifacts"][skill_name] = main_content
        shared["bibtex_entries"].extend(bib_entries)

        # Create short summary for history (first 300 chars)
        summary = main_content[:300].replace("\n", " ")
        if len(main_content) > 300:
            summary += "..."
        shared["history"].append({
            "step": len(shared["history"]) + 1,
            "skill": skill_name,
            "summary": summary,
            "cost": usage["cost"],
        })

        print(f"[ExecuteSkill] Completed: {skill_name} (${usage['cost']:.4f})")
        print(f"[ExecuteSkill] BibTeX entries found: {len(bib_entries)}")
        print(f"[ExecuteSkill] Budget remaining: ${shared['budget_remaining']:.4f}")
        return "decide"


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

        return {
            "topic": shared["topic"],
            "artifacts": shared.get("artifacts", {}),
            "cite_keys": cite_keys,
            "has_methods": has_methods,
            "has_results": has_results,
            "report_type": report_type,
            "writing_guide": writing_guide,
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

        prompt = f"""You are writing a scientific {report_type.lower()} as compilable LaTeX.
{quality_block}
## Research Topic
{prep_res["topic"]}

## Research Artifacts (your source material)
{artifact_text}

## Available BibTeX cite keys
{cite_list}

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

        title = title_match.group(1).strip() if title_match else prep_res["topic"]
        abstract = abstract_match.group(1).strip() if abstract_match else "Abstract not available."
        body = body_match.group(1).strip() if body_match else text.strip()

        # Assemble .tex from skeleton
        tex = LATEX_SKELETON
        tex = tex.replace("%% TITLE %%", title)
        tex = tex.replace("%% ABSTRACT %%", abstract)
        tex = tex.replace("%% BODY %%", body)

        # Deduplicate and write .bib
        bib_content = dedup_bibtex(shared.get("bibtex_entries", []))

        # Create output directory
        task_id = str(uuid.uuid4())
        out_dir = Path(shared.get("output_dir", "outputs")) / task_id
        out_dir.mkdir(parents=True, exist_ok=True)

        (out_dir / "report.tex").write_text(tex, encoding="utf-8")
        (out_dir / "references.bib").write_text(bib_content, encoding="utf-8")

        shared["tex_content"] = tex
        shared["bib_content"] = bib_content
        shared["output_path"] = str(out_dir)

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
        if success:
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
    """Fix LaTeX compilation errors using the error log."""

    def prep(self, shared):
        return {
            "tex_content": shared["tex_content"],
            "errors": shared.get("compile_errors", ""),
            "attempt": shared.get("fix_attempts", 0),
        }

    def exec(self, prep_res):
        if prep_res["attempt"] >= 2:
            # Give up after 2 fix attempts
            return None, {"input_tokens": 0, "output_tokens": 0, "cost": 0}

        # Extract just the error lines to save tokens
        error_lines = []
        for line in prep_res["errors"].split("\n"):
            if line.startswith("!") or "Error" in line or "Undefined" in line:
                error_lines.append(line)
        error_summary = "\n".join(error_lines[:30])  # cap at 30 lines

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

        # Write fixed .tex
        out_dir = Path(shared["output_path"])
        (out_dir / "report.tex").write_text(cleaned, encoding="utf-8")
        shared["tex_content"] = cleaned

        print(f"[FixTeX] Applied fix (attempt {shared['fix_attempts']})")
        return "compile"


# ===================================================================
# 7. Finisher — terminal node (no successors → flow ends cleanly)
# ===================================================================
class Finisher(Node):
    """Print cost summary and end the flow."""

    def prep(self, shared):
        return shared

    def exec(self, prep_res):
        return None

    def post(self, shared, prep_res, exec_res):
        total = sum(entry["cost"] for entry in shared.get("cost_log", []))
        print(f"\n{'='*50}")
        print(f"Research complete!")
        print(f"Total cost: ${total:.4f}")
        print(f"Budget used: ${total:.4f} / ${shared['budget_dollars']:.2f}")
        print(f"Output: {shared['output_path']}/")
        print(f"{'='*50}")
