#!/usr/bin/env python3
"""
Autonomous Research Agent using PocketFlow + OpenRouter

Executes scientific-skills pipeline with adaptive budget control
Input: Research question
Output: PDF paper (brief/technical/conference based on complexity)
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

from pocketflow import Flow, Node
from utils.llm import call_llm, parse_json, parse_response
from utils.skill_loader import get_skill_instructions
from utils.docker_runner import run_experiment

# Load environment variables
load_dotenv()

# Configuration
ENABLE_DOCKER = os.getenv("ENABLE_DOCKER", "False").lower() == "true"
DOCKER_IMAGE = os.getenv("DOCKER_IMAGE", "python:3.13")
MAX_CONTEXT_CHARS = 3000

# Virtual environment path for local execution
VENV_PATH = os.path.join(os.getcwd(), ".venv")
VENV_PYTHON = os.path.join(VENV_PATH, "bin", "python3")

# Stage-specific environment configuration
# Survey and paper writing ALWAYS use local .venv
# Method implementation and evaluation can use Docker (if ENABLE_DOCKER=true) or local .venv
STAGE_USE_DOCKER = {
    "survey": False,                           # Always local .venv
    "method": ENABLE_DOCKER,                   # Docker if enabled, else local .venv
    "evaluation": ENABLE_DOCKER,               # Docker if enabled, else local .venv
    "paper": False                             # Always local .venv
}

# Per-stage budget allocation (fractions of max_rounds)
STAGE_BUDGET = {
    "survey": 0.15,       # 15% - literature survey
    "method": 0.35,       # 35% - method implementation
    "evaluation": 0.30,   # 30% - experimental evaluation
    "paper": 0.20,        # 20% - paper writing (single pass)
}
# Max iterations per stage (hard cap regardless of budget)
MAX_ITERATIONS = {"survey": 2, "method": 3, "evaluation": 2}

# Response format instructions shared by all content-producing nodes
RESPONSE_FORMAT = """
RESPONSE FORMAT:
1. First, output a small JSON control block (status, rounds_used, reasoning only - NO file content):
```json
{...}
```
2. Then output each file as a raw section (no JSON escaping needed):
===FILE: filename.ext===
raw content here
===FILE: another.ext===
more content
===END===
"""


def build_system_prompt(question):
    """Build a system message that anchors the research question as the primary directive."""
    return (
        f"You are a research assistant working on a specific research project.\n"
        f"YOUR PRIMARY RESEARCH QUESTION: {question}\n\n"
        f"CRITICAL: Every output you produce — surveys, code, experiments, and papers — "
        f"MUST be specifically and directly about this research question. "
        f"Do NOT produce generic frameworks or methodologies. "
        f"Your work must concretely address the specific subject, platform, or topic named in the question above. "
        f"If the question mentions a specific URL, platform, or community, your output must focus on THAT specific target."
    )


def truncate_context(data, max_chars=MAX_CONTEXT_CHARS):
    """Truncate large data for prompt inclusion to avoid context overflow."""
    text = json.dumps(data, indent=2) if isinstance(data, (dict, list)) else str(data)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... (truncated, {len(text)} total chars)"


def summarize_files(files, max_per_file=1500):
    """Create a brief summary of file contents for prompt context."""
    if not files:
        return "(none)"
    parts = []
    for name, content in files.items():
        preview = content[:max_per_file]
        if len(content) > max_per_file:
            preview += f"... ({len(content)} chars total)"
        parts.append(f"[{name}]: {preview}")
    return "\n".join(parts)


# ============================================================================
# Node Definitions
# ============================================================================

class ClassifyComplexityNode(Node):
    """Analyze research question and determine budget (max_rounds)"""

    def prep(self, shared):
        return shared["question"]

    def exec(self, question):
        prompt = f"""Analyze this research question and classify its complexity:

Question: {question}

Consider:
1. Scope: Single algorithm vs system comparison vs novel method
2. Implementation effort: Toy example vs full system vs experiments
3. Evaluation depth: Basic metrics vs ablations vs statistical tests
4. Expected output: Brief document vs technical report vs conference paper

Return JSON only:
```json
{{
  "complexity": "low or medium or high",
  "reasoning": "brief explanation",
  "max_rounds": 30,
  "expected_output": "brief or technical or conference"
}}
```

max_rounds guide: 30 for low, 100 for medium, 300 for high.
Be conservative - if uncertain, choose lower complexity.
"""
        return call_llm(prompt, max_tokens=1024)

    def post(self, shared, prep_res, exec_res):
        try:
            result = parse_json(exec_res)
            shared["complexity"] = result["complexity"]
            shared["max_rounds"] = result["max_rounds"]
            shared["expected_output"] = result.get("expected_output", "technical")
            shared["current_round"] = 0

            print(f"\n{'='*80}")
            print(f"Complexity: {result['complexity'].upper()} | "
                  f"Max Rounds: {result['max_rounds']} | "
                  f"Output: {result.get('expected_output', 'technical')}")
            print(f"Reasoning: {result.get('reasoning', '')[:100]}")
            print(f"{'='*80}\n")
        except Exception as e:
            print(f"⚠️  Classification failed: {e}. Defaulting to medium.")
            shared["complexity"] = "medium"
            shared["max_rounds"] = 100
            shared["expected_output"] = "technical"
            shared["current_round"] = 0

        return "default"


class BudgetGuardNode(Node):
    """Check if budget allows proceeding to next stage"""

    def __init__(self, stage_name):
        super().__init__()
        self.stage_name = stage_name

    def exec(self, _):
        pass

    def post(self, shared, prep_res, exec_res):
        current = shared["current_round"]
        max_rounds = shared["max_rounds"]

        if current < max_rounds:
            print(f"✓ Budget check passed: {current}/{max_rounds} rounds used")
            return "proceed"
        else:
            print(f"⚠️  Budget exceeded at {self.stage_name}: {current}/{max_rounds}")
            shared["budget_exceeded"] = True
            shared["termination_stage"] = self.stage_name
            return "emergency_report"


class LiteratureSurveyNode(Node):
    """Execute literature-survey skill with iteration support"""

    def __init__(self):
        super().__init__()
        self.skill = get_skill_instructions("scientific-skills/literature-survey/SKILL.md")

    def prep(self, shared):
        stage_rounds = int(shared["max_rounds"] * STAGE_BUDGET["survey"])
        iteration = shared.get("survey_iteration", 0)
        return {
            "skill": self.skill,
            "question": shared["question"],
            "prev_files": shared.get("survey_files"),
            "stage_rounds": stage_rounds,
            "iteration": iteration,
            "is_final": iteration + 1 >= MAX_ITERATIONS["survey"],
        }

    def exec(self, inputs):
        prev_context = ""
        if inputs["prev_files"]:
            prev_context = f"Previous Output (refine if needed):\n{summarize_files(inputs['prev_files'])}"
        else:
            prev_context = "First iteration - start from scratch."

        urgency = ""
        if inputs["is_final"]:
            urgency = "\n**THIS IS YOUR FINAL ITERATION. You MUST return status: done and deliver complete output.**\n"

        system_prompt = build_system_prompt(inputs['question'])

        prompt = f"""RESEARCH QUESTION (this is your primary focus): {inputs['question']}

Execute the literature survey skill below. Your survey MUST specifically address the research question above — not a generic survey of the field.

Skill instructions:
{inputs['skill']}

Stage Budget: {inputs['stage_rounds']} rounds (for this stage only)
Current Iteration: {inputs['iteration']} of {MAX_ITERATIONS['survey']} max
{urgency}
{prev_context}

{RESPONSE_FORMAT}

JSON control block should contain:
- "status": "continue" or "done"
- "rounds_used": number (keep to 1)
- "reasoning": "why continue or done"
- "key_papers": ["paper1", "paper2"]

File sections to include:
===FILE: survey_report.md===  (comprehensive markdown literature survey)
===FILE: references.bib===    (BibTeX bibliography)
===END===

If status is "done", ensure the survey is comprehensive and publication-ready.
Prefer returning "done" with complete output over multiple iterations.

REMINDER: Your survey must be specifically about: {inputs['question']}
"""
        return call_llm(prompt, max_tokens=8192, system_prompt=system_prompt)

    def post(self, shared, prep_res, exec_res):
        try:
            resp = parse_response(exec_res)
            ctrl = resp["control"]
            files = resp["files"]

            shared["survey_files"] = files
            shared["survey_control"] = ctrl
            shared["current_round"] += ctrl.get("rounds_used", 1)
            shared["survey_iteration"] = shared.get("survey_iteration", 0) + 1

            status = ctrl.get("status", "done")
            if shared["survey_iteration"] >= MAX_ITERATIONS["survey"] and status == "continue":
                print(f"   ⚠️  Max iterations reached, forcing done")
                status = "done"

            print(f"\n📚 Literature Survey - Iteration {shared['survey_iteration']}")
            print(f"   Status: {status} | Rounds: {shared['current_round']}/{shared['max_rounds']}")
            print(f"   Files: {list(files.keys())}")

            return status
        except Exception as e:
            print(f"⚠️  Literature survey failed: {e}")
            shared["survey_files"] = {}
            shared["current_round"] += 1
            return "done"


class MethodImplementationNode(Node):
    """Execute method-implementation skill with iteration support"""

    def __init__(self):
        super().__init__()
        self.skill = get_skill_instructions("scientific-skills/method-implementation/SKILL.md")

    def prep(self, shared):
        stage_rounds = int(shared["max_rounds"] * STAGE_BUDGET["method"])
        iteration = shared.get("method_iteration", 0)
        return {
            "skill": self.skill,
            "question": shared["question"],
            "survey_files": shared.get("survey_files", {}),
            "prev_files": shared.get("method_files"),
            "stage_rounds": stage_rounds,
            "iteration": iteration,
            "is_final": iteration + 1 >= MAX_ITERATIONS["method"],
        }

    def exec(self, inputs):
        survey_ctx = summarize_files(inputs["survey_files"])
        prev_context = ""
        if inputs["prev_files"]:
            prev_context = f"Previous Output:\n{summarize_files(inputs['prev_files'])}"
        else:
            prev_context = "First iteration."

        urgency = ""
        if inputs["is_final"]:
            urgency = "\n**THIS IS YOUR FINAL ITERATION. You MUST return status: done and deliver complete, working code.**\n"

        system_prompt = build_system_prompt(inputs['question'])

        prompt = f"""RESEARCH QUESTION (this is your primary focus): {inputs['question']}

Execute the method implementation skill below. Your implementation MUST specifically address the research question above — not a generic framework.

Skill instructions:
{inputs['skill']}

Literature Survey Summary: {survey_ctx}
Stage Budget: {inputs['stage_rounds']} rounds (for this stage only)
Current Iteration: {inputs['iteration']} of {MAX_ITERATIONS['method']} max
{urgency}
{prev_context}

{RESPONSE_FORMAT}

JSON control block should contain:
- "status": "continue" or "done"
- "rounds_used": number (keep to 1)
- "reasoning": "why continue or done"
- "requirements_txt": "pip dependencies, one per line"

File sections to include:
===FILE: design.md===         (method design document)
===FILE: main.py===           (primary implementation code)
(add more ===FILE: xxx.py=== sections for additional source files as needed)
===END===

Deliver ALL code in a single iteration when possible. Only use "continue" if the implementation genuinely needs more work.

REMINDER: Your implementation must be specifically about: {inputs['question']}
"""
        return call_llm(prompt, max_tokens=8192, system_prompt=system_prompt)

    def post(self, shared, prep_res, exec_res):
        try:
            resp = parse_response(exec_res)
            ctrl = resp["control"]
            files = resp["files"]

            shared["method_files"] = files
            shared["method_control"] = ctrl
            shared["current_round"] += ctrl.get("rounds_used", 1)
            shared["method_iteration"] = shared.get("method_iteration", 0) + 1

            status = ctrl.get("status", "done")
            if shared["method_iteration"] >= MAX_ITERATIONS["method"] and status == "continue":
                print(f"   ⚠️  Max iterations reached, forcing done")
                status = "done"

            print(f"\n🔧 Method Implementation - Iteration {shared['method_iteration']}")
            print(f"   Status: {status} | Rounds: {shared['current_round']}/{shared['max_rounds']}")
            print(f"   Files: {list(files.keys())}")

            return status
        except Exception as e:
            print(f"⚠️  Method implementation failed: {e}")
            shared["method_files"] = {}
            shared["current_round"] += 1
            return "done"


class ExperimentalEvaluationNode(Node):
    """Execute experiments using Docker or locally"""

    def __init__(self):
        super().__init__()
        self.skill = get_skill_instructions("scientific-skills/experimental-evaluation/SKILL.md")

    def prep(self, shared):
        stage_rounds = int(shared["max_rounds"] * STAGE_BUDGET["evaluation"])
        iteration = shared.get("evaluation_iteration", 0)
        return {
            "skill": self.skill,
            "question": shared["question"],
            "method_files": shared.get("method_files", {}),
            "method_control": shared.get("method_control", {}),
            "previous_results": shared.get("evaluation_output"),
            "stage_rounds": stage_rounds,
            "iteration": iteration,
            "is_final": iteration + 1 >= MAX_ITERATIONS["evaluation"],
            "output_dir": shared.get("output_dir"),
        }

    def exec(self, inputs):
        method_ctx = summarize_files(inputs["method_files"])
        reqs = inputs["method_control"].get("requirements_txt", "")

        urgency = ""
        if inputs["is_final"]:
            urgency = "\n**THIS IS YOUR FINAL ITERATION. You MUST return status: done and deliver complete experiment code.**\n"

        system_prompt = build_system_prompt(inputs['question'])

        prompt = f"""RESEARCH QUESTION (this is your primary focus): {inputs['question']}

Execute the experimental evaluation skill below. Your experiments MUST specifically evaluate the research question above — not generic benchmarks.

Skill instructions:
{inputs['skill']}

Method Implementation Summary: {method_ctx}
Known Dependencies: {reqs}
Stage Budget: {inputs['stage_rounds']} rounds (for this stage only)
Current Iteration: {inputs['iteration']} of {MAX_ITERATIONS['evaluation']} max
{urgency}
Generate self-contained experiment code that will:
1. Run the implemented method
2. Collect metrics
3. Generate plots and tables

{RESPONSE_FORMAT}

JSON control block should contain:
- "status": "continue" or "done"
- "rounds_used": number (keep to 1)
- "reasoning": "why continue or done"
- "expected_outputs": ["results.csv", "figures/plot1.png"]

File sections to include:
===FILE: experiment.py===      (self-contained experiment script)
===FILE: requirements.txt===   (pip dependencies)
===END===

The experiment code must be fully self-contained and save all results to files.
Deliver complete experiment code in a single iteration when possible.

REMINDER: Your experiments must be specifically about: {inputs['question']}
"""
        response = call_llm(prompt, max_tokens=8192, system_prompt=system_prompt)
        try:
            resp = parse_response(response)
        except ValueError as e:
            print(f"  ⚠️  Eval parse error: {e}")
            return json.dumps({"status": "done", "rounds_used": 1,
                               "reasoning": "Failed to parse LLM response",
                               "files": {}})

        ctrl = resp["control"]
        files = resp["files"]

        # Run experiment if code was generated
        if "experiment.py" in files:
            use_docker = STAGE_USE_DOCKER.get("evaluation", True)
            print(f"\n🧪 Running experiment ({'Docker' if use_docker else 'Local'})...")
            task_id = f"eval_{inputs['iteration']}"
            exec_result = run_experiment(
                {
                    "experiment_py": files["experiment.py"],
                    "requirements_txt": files.get("requirements.txt", ""),
                    "task_id": task_id
                },
                mode="docker" if use_docker else "local",
                image=DOCKER_IMAGE,
                output_dir=inputs.get("output_dir")
            )
            ctrl["experiment_output"] = exec_result
            print(f"   Exit code: {exec_result['exit_code']}")
            print(f"   Workspace: {exec_result['workspace']}")

        # Pack control + files for post()
        ctrl["_files"] = files
        return json.dumps(ctrl, default=str)

    def post(self, shared, prep_res, exec_res):
        try:
            result = json.loads(exec_res)
            files = result.pop("_files", {})

            shared["evaluation_output"] = result
            shared["evaluation_files"] = files
            shared["current_round"] += result.get("rounds_used", 1)
            shared["evaluation_iteration"] = shared.get("evaluation_iteration", 0) + 1

            status = result.get("status", "done")
            if shared["evaluation_iteration"] >= MAX_ITERATIONS["evaluation"] and status == "continue":
                print(f"   ⚠️  Max iterations reached, forcing done")
                status = "done"

            print(f"\n📊 Experimental Evaluation - Iteration {shared['evaluation_iteration']}")
            print(f"   Status: {status} | Rounds: {shared['current_round']}/{shared['max_rounds']}")
            print(f"   Files: {list(files.keys())}")

            return status
        except Exception as e:
            print(f"⚠️  Experimental evaluation failed: {e}")
            shared["evaluation_output"] = {"error": str(e)}
            shared["evaluation_files"] = {}
            shared["current_round"] += 1
            return "done"


class PaperWritingNode(Node):
    """Generate final PDF paper"""

    def __init__(self, emergency=False):
        super().__init__()
        self.skill = get_skill_instructions("scientific-skills/paper-writing/SKILL.md")
        self.emergency = emergency

    def prep(self, shared):
        return {
            "skill": self.skill,
            "question": shared["question"],
            "survey_files": shared.get("survey_files", {}),
            "method_files": shared.get("method_files", {}),
            "evaluation_files": shared.get("evaluation_files", {}),
            "evaluation_output": shared.get("evaluation_output", {}),
            "expected_output": shared.get("expected_output", "technical"),
            "emergency": self.emergency
        }

    def exec(self, inputs):
        mode = "EMERGENCY - Brief summary" if inputs["emergency"] else "Full paper"

        system_prompt = build_system_prompt(inputs['question'])

        prompt = f"""RESEARCH QUESTION (this is your primary focus): {inputs['question']}

Generate an academic paper ({mode}) that is SPECIFICALLY about the research question above. The paper title, abstract, introduction, and all sections must directly reference and focus on the specific subject of the research question.

Expected Output Type: {inputs['expected_output']}

Skill instructions:
{inputs['skill']}

Available Content:
- Literature Survey: {summarize_files(inputs['survey_files'])}
- Method: {summarize_files(inputs['method_files'])}
- Evaluation: {summarize_files(inputs['evaluation_files'])}
- Experiment Results: {truncate_context(inputs['evaluation_output'], 1000)}

{"EMERGENCY MODE: Budget exceeded. Generate brief summary with available results." if inputs['emergency'] else ""}

{RESPONSE_FORMAT}

JSON control block should contain:
- "title": "paper title"
- "abstract": "paper abstract"

File sections to include:
===FILE: main.tex===         (complete LaTeX paper)
===FILE: references.bib===   (BibTeX bibliography)
===END===

IMPORTANT LaTeX requirements (we compile with Tectonic/XeTeX):
- Do NOT use \\usepackage[...]{{inputenc}} — XeTeX handles UTF-8 natively
- Do NOT use \\usepackage[T1]{{fontenc}} — not needed with XeTeX
- Use \\usepackage{{fontspec}} if custom fonts are needed (usually not required)
- Standard packages (amsmath, graphicx, hyperref, booktabs, algorithm, listings, natbib, geometry) are fine

CROSS-REFERENCE RULES (no "??" in output):
- Every \\ref{{X}} MUST have a matching \\label{{X}} defined in the document
- \\label MUST come AFTER \\caption inside every table/figure environment
- Do NOT use \\includegraphics — no image files exist in the build directory. Use tables or \\rule placeholders instead.
- Use prefixes: tab: for tables, fig: for figures, eq: for equations, alg: for algorithms
- Use non-breaking space: Table~\\ref{{tab:X}}, Figure~\\ref{{fig:X}}

Include all standard sections: Abstract, Introduction, Related Work, Method, Experiments, Conclusion.

REMINDER: The paper must be specifically about: {inputs['question']}
Do NOT write a generic paper about online communities or computational frameworks. The title and every section must reference the specific subject from the research question.
"""
        return call_llm(prompt, max_tokens=12000, system_prompt=system_prompt)

    def post(self, shared, prep_res, exec_res):
        try:
            resp = parse_response(exec_res)
            ctrl = resp["control"]
            files = resp["files"]

            shared["paper_control"] = ctrl
            shared["paper_files"] = files

            print(f"\n📄 Paper Writing Complete")
            print(f"   Title: {ctrl.get('title', 'Untitled')}")
            print(f"   Files: {list(files.keys())}")
            print(f"   Mode: {'Emergency' if self.emergency else 'Full paper'}")

            if not files:
                print(f"   ⚠️  No file sections found in paper response")
                self._save_raw_response(shared, exec_res)

            return "default"
        except Exception as e:
            print(f"⚠️  Paper writing failed: {e}")
            self._save_raw_response(shared, exec_res)
            shared["paper_files"] = {}
            return "default"

    @staticmethod
    def _save_raw_response(shared, raw_response):
        """Save raw LLM response for debugging when paper parsing fails."""
        output_dir = Path(shared.get("output_dir", "."))
        debug_file = output_dir / "paper" / "raw_response.txt"
        debug_file.parent.mkdir(parents=True, exist_ok=True)
        debug_file.write_text(raw_response or "(empty)")
        print(f"   Saved raw response to: {debug_file}")


def _repair_latex(tex_content):
    """
    Auto-repair common LLM-generated LaTeX errors before compilation.
    Returns repaired content and a list of repairs made.
    """
    import re as _re
    repairs = []

    # 1. Remove inputenc (XeTeX handles UTF-8 natively)
    new = _re.sub(r'\\usepackage\[.*?\]\{inputenc\}\s*\n?', '', tex_content)
    if new != tex_content:
        repairs.append("removed inputenc (XeTeX native UTF-8)")
        tex_content = new

    # 2. Remove T1 fontenc (not needed with XeTeX)
    new = _re.sub(r'\\usepackage\[T1\]\{fontenc\}\s*\n?', '', tex_content)
    if new != tex_content:
        repairs.append("removed T1 fontenc")
        tex_content = new

    # 3. Fix tabular column count mismatches
    # Find each tabular environment: match column spec and all rows up to \end{tabular}
    tabular_re = _re.compile(
        r'(\\begin\{tabular\}\{)([^}]+)(\})(.*?)(\\end\{tabular\})',
        _re.DOTALL
    )
    def _fix_tabular(m):
        prefix, col_spec, brace, body, end = m.groups()
        # Count columns in spec (count letters: l, c, r, p, m, b — ignore |, @, !, etc.)
        spec_cols = len(_re.findall(r'[lcrpmb]', col_spec))
        # Find max columns in any row (count & separators + 1)
        max_row_cols = 0
        for row in _re.split(r'\\\\', body):
            # Skip empty rows or \hline/\midrule etc.
            stripped = row.strip()
            if not stripped or stripped.startswith('\\') and '&' not in stripped:
                continue
            n_cols = stripped.count('&') + 1
            max_row_cols = max(max_row_cols, n_cols)
        if max_row_cols > spec_cols:
            # Extend column spec: add 'r' columns for the difference
            extra = max_row_cols - spec_cols
            # Preserve alignment pattern: use the last column type
            last_type = _re.findall(r'[lcrpmb]', col_spec)
            pad_char = last_type[-1] if last_type else 'r'
            new_spec = col_spec + pad_char * extra
            repairs.append(f"fixed tabular: {spec_cols} cols -> {max_row_cols} cols")
            return prefix + new_spec + brace + body + end
        return m.group(0)

    tex_content = tabular_re.sub(_fix_tabular, tex_content)

    # 4. Fix \includegraphics references (no image files exist in build dir)
    # Replace with a \rule placeholder
    def _fix_includegraphics(m):
        repairs.append(f"replaced \\includegraphics with placeholder")
        return r'\rule{0.8\textwidth}{3cm}'
    tex_content = _re.sub(
        r'\\includegraphics\[.*?\]\{.*?\}',
        _fix_includegraphics,
        tex_content
    )
    tex_content = _re.sub(
        r'\\includegraphics\{.*?\}',
        _fix_includegraphics,
        tex_content
    )

    # 5. Fix unclosed environments (count begin/end pairs per environment name)
    env_re = _re.compile(r'\\(begin|end)\{(\w+)\}')
    env_counts = {}  # env_name -> (begin_count, end_count)
    for m in env_re.finditer(tex_content):
        action, env_name = m.group(1), m.group(2)
        if env_name == 'document':
            continue  # don't touch the document environment
        b, e = env_counts.get(env_name, (0, 0))
        if action == 'begin':
            env_counts[env_name] = (b + 1, e)
        else:
            env_counts[env_name] = (b, e + 1)
    unclosed = []
    for env_name, (b, e) in env_counts.items():
        for _ in range(b - e):
            unclosed.append(env_name)
    if unclosed:
        close_cmds = '\n'.join(f'\\end{{{env}}}' for env in reversed(unclosed))
        end_doc = tex_content.rfind(r'\end{document}')
        if end_doc != -1:
            tex_content = tex_content[:end_doc] + close_cmds + '\n' + tex_content[end_doc:]
        else:
            tex_content += '\n' + close_cmds
        repairs.append(f"closed {len(unclosed)} unclosed environment(s): {unclosed}")

    return tex_content, repairs


def compile_latex(paper_dir):
    """
    Compile LaTeX to PDF. Priority: tectonic > pdflatex > Docker.
    Returns True if PDF was generated.
    """
    import subprocess
    import shutil

    tex_file = paper_dir / "main.tex"
    if not tex_file.exists():
        return False

    # Pre-process: auto-repair common LLM LaTeX errors
    tex_content = tex_file.read_text()
    patched, repairs = _repair_latex(tex_content)
    if repairs:
        print(f"  [REPAIR] Applied {len(repairs)} LaTeX fix(es):")
        for r in repairs:
            print(f"    - {r}")
    if patched != tex_content:
        tex_file.write_text(patched)

    # 1. Try Tectonic (preferred - single binary, auto-downloads packages)
    tectonic_bin = shutil.which("tectonic") or str(Path.home() / ".local/bin/tectonic")
    if Path(tectonic_bin).exists():
        print("  Compiling LaTeX (tectonic)...")
        try:
            result = subprocess.run(
                [tectonic_bin, "main.tex"],
                cwd=paper_dir, capture_output=True, text=True, timeout=300
            )
            if (paper_dir / "main.pdf").exists():
                print("  PDF generated successfully (tectonic)")
                return True
            else:
                print(f"  Tectonic failed: {result.stderr[-500:]}")
        except subprocess.TimeoutExpired:
            print("  Tectonic timed out")

    # 2. Try local pdflatex
    if shutil.which("pdflatex"):
        print("  Compiling LaTeX (pdflatex)...")
        try:
            for _ in range(2):
                subprocess.run(
                    ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
                    cwd=paper_dir, capture_output=True, timeout=120
                )
            if shutil.which("bibtex") and (paper_dir / "references.bib").exists():
                subprocess.run(["bibtex", "main"], cwd=paper_dir, capture_output=True, timeout=60)
                subprocess.run(
                    ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
                    cwd=paper_dir, capture_output=True, timeout=120
                )
            if (paper_dir / "main.pdf").exists():
                print("  PDF generated successfully (pdflatex)")
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # 3. Try Docker
    if ENABLE_DOCKER or shutil.which("docker"):
        print("  Compiling LaTeX (Docker texlive)...")
        try:
            vol = str(paper_dir.resolve())
            cmd = (
                "pdflatex -interaction=nonstopmode -halt-on-error main.tex && "
                "bibtex main 2>/dev/null; "
                "pdflatex -interaction=nonstopmode -halt-on-error main.tex && "
                "pdflatex -interaction=nonstopmode -halt-on-error main.tex"
            )
            subprocess.run(
                ["docker", "run", "--rm", "-v", f"{vol}:/work", "-w", "/work",
                 "texlive/texlive:latest", "bash", "-c", cmd],
                capture_output=True, timeout=300
            )
            if (paper_dir / "main.pdf").exists():
                print("  PDF generated successfully (Docker)")
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    print("  ⚠️  Could not compile PDF.")
    print("  Install: python3 utils/install_tectonic.py")
    print("  Or manual: cd paper/ && tectonic main.tex")
    return False


class SaveArtifactsNode(Node):
    """Save all artifacts to disk"""

    def __init__(self, stage_name):
        super().__init__()
        self.stage_name = stage_name

    def exec(self, inputs):
        pass

    def post(self, shared, prep_res, exec_res):
        output_dir = Path(shared["output_dir"])

        # Save full state snapshot (exclude large file content)
        state_file = output_dir / "state.json"
        state_keys = ["question", "complexity", "max_rounds", "current_round",
                      "expected_output", "start_time", "output_dir",
                      "survey_control", "method_control", "evaluation_output",
                      "paper_control", "budget_exceeded", "termination_stage"]
        state = {k: shared[k] for k in state_keys if k in shared}
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2, default=str)

        # Save files from the relevant stage
        stage_files_key = f"{self.stage_name}_files"
        files = shared.get(stage_files_key, {})

        if files:
            stage_dir = output_dir / self.stage_name
            stage_dir.mkdir(exist_ok=True)
            for filename, content in files.items():
                filepath = stage_dir / filename
                filepath.parent.mkdir(parents=True, exist_ok=True)
                filepath.write_text(content)

        # Compile LaTeX to PDF for paper stage
        if self.stage_name == "paper" and files:
            paper_dir = output_dir / "paper"
            compile_latex(paper_dir)

        print(f"💾 Saved {self.stage_name} artifacts ({len(files)} files) to: {output_dir}")

        return "default"


# ============================================================================
# Flow Construction
# ============================================================================

def create_research_pipeline():
    """Build the complete research pipeline flow"""

    classify = ClassifyComplexityNode()

    guard1 = BudgetGuardNode("literature_survey")
    survey = LiteratureSurveyNode()
    save1 = SaveArtifactsNode("survey")

    guard2 = BudgetGuardNode("method_implementation")
    method = MethodImplementationNode()
    save2 = SaveArtifactsNode("method")

    guard3 = BudgetGuardNode("experimental_evaluation")
    evaluation = ExperimentalEvaluationNode()
    save3 = SaveArtifactsNode("evaluation")

    paper = PaperWritingNode(emergency=False)
    emergency_paper = PaperWritingNode(emergency=True)
    save_final = SaveArtifactsNode("paper")

    pipeline = Flow(start=classify)

    classify >> guard1
    guard1 - "proceed" >> survey
    guard1 - "emergency_report" >> emergency_paper

    survey - "continue" >> survey
    survey - "done" >> save1 >> guard2

    guard2 - "proceed" >> method
    guard2 - "emergency_report" >> emergency_paper

    method - "continue" >> method
    method - "done" >> save2 >> guard3

    guard3 - "proceed" >> evaluation
    guard3 - "emergency_report" >> emergency_paper

    evaluation - "continue" >> evaluation
    evaluation - "done" >> save3 >> paper

    paper >> save_final
    emergency_paper >> save_final

    return pipeline


# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py '<research question>'")
        print("\nExample:")
        print("  python main.py 'Compare quicksort vs mergesort performance'")
        sys.exit(1)

    question = " ".join(sys.argv[1:])

    print(f"\n{'='*80}")
    print("Mini Researcher Agent - PocketFlow Edition")
    print(f"{'='*80}")
    print(f"\nResearch Question: {question}")
    print(f"Environment Configuration:")
    print(f"  Survey: Local (.venv)")
    method_env = "Docker" if ENABLE_DOCKER else "Local (.venv)"
    eval_env = "Docker" if ENABLE_DOCKER else "Local (.venv)"
    print(f"  Method: {method_env}" + (f" (Image: {DOCKER_IMAGE})" if ENABLE_DOCKER else ""))
    print(f"  Evaluation: {eval_env}" + (f" (Image: {DOCKER_IMAGE})" if ENABLE_DOCKER else ""))
    print(f"  Paper: Local (.venv)")
    print(f"Model: {os.getenv('OPENROUTER_MODEL', 'anthropic/claude-haiku-4.5')}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    question_hash = abs(hash(question)) % 10000
    output_dir = Path(f"research_outputs/{timestamp}_{question_hash}")
    output_dir.mkdir(parents=True, exist_ok=True)

    shared = {
        "question": question,
        "start_time": datetime.now().isoformat(),
        "output_dir": str(output_dir)
    }

    try:
        pipeline = create_research_pipeline()
        pipeline.run(shared)

        print(f"\n{'='*80}")
        print("Research Complete!")
        print(f"{'='*80}")
        print(f"Complexity: {shared.get('complexity', 'unknown').upper()}")
        print(f"Rounds Used: {shared.get('current_round', 0)}/{shared.get('max_rounds', 0)}")
        print(f"Output Directory: {shared.get('output_dir', 'N/A')}")

        if shared.get("budget_exceeded"):
            print(f"\n⚠️  Budget exceeded at: {shared.get('termination_stage', 'unknown')}")
            print("   Emergency paper generated with partial results.")

        print(f"\n{'='*80}\n")

    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Pipeline failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
