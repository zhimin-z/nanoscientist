"""
MSR-Scientist: A minimalist self-evolving researcher agent using smolagents.

Ultra-minimal implementation - everything in one file.
"""

from smolagents import CodeAgent, HfApiModel, tool
from typing import Optional, Any
import subprocess
import json
import os
import sys
import tempfile
from pathlib import Path


# ============================================================================
# TOOLS
# ============================================================================

@tool
def run_experiment(code: str, language: str = "python") -> str:
    """
    Execute experimental code and return results.

    Args:
        code: The code to execute
        language: "python" or "bash"

    Returns:
        JSON string with stdout, stderr, and return code
    """
    result = {"stdout": "", "stderr": "", "return_code": 0}

    try:
        if language == "python":
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(code)
                temp_file = f.name
            try:
                process = subprocess.run(
                    [sys.executable, temp_file],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                result["stdout"] = process.stdout
                result["stderr"] = process.stderr
                result["return_code"] = process.returncode
            finally:
                os.unlink(temp_file)
        else:  # bash
            process = subprocess.run(
                code,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )
            result["stdout"] = process.stdout
            result["stderr"] = process.stderr
            result["return_code"] = process.returncode
    except Exception as e:
        result["stderr"] = str(e)
        result["return_code"] = -1

    return json.dumps(result, indent=2)


@tool
def install_package(package_name: str) -> str:
    """
    Install a Python package using pip.

    Args:
        package_name: Package name (e.g., "numpy" or "numpy>=1.20.0")

    Returns:
        JSON string with installation status
    """
    result = {"package": package_name, "status": "installing"}

    try:
        process = subprocess.run(
            [sys.executable, "-m", "pip", "install", package_name],
            capture_output=True,
            text=True,
            timeout=120
        )
        if process.returncode == 0:
            result["status"] = "success"
            result["message"] = f"Installed {package_name}"
        else:
            result["status"] = "error"
            result["message"] = process.stderr
    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)

    return json.dumps(result, indent=2)


@tool
def draft_paper(
    title: str,
    authors: str,
    abstract: str,
    sections: str,
    output_dir: str = "outputs"
) -> str:
    """
    Generate a LaTeX research paper.

    Args:
        title: Paper title
        authors: Authors (comma-separated)
        abstract: Abstract text
        sections: JSON string with sections [{"title": "...", "content": "..."}]
        output_dir: Output directory

    Returns:
        JSON string with file paths
    """
    result = {"title": title, "latex_file": None, "pdf_file": None, "error": None}

    try:
        from pylatex import Document, Section, Command
        from pylatex.utils import NoEscape

        os.makedirs(output_dir, exist_ok=True)
        sections_data = json.loads(sections) if isinstance(sections, str) else sections

        # Create document
        doc = Document()
        doc.preamble.append(Command('title', title))
        doc.preamble.append(Command('author', authors))
        doc.preamble.append(Command('date', NoEscape(r'\today')))
        doc.append(NoEscape(r'\maketitle'))

        # Abstract
        with doc.create(Section('Abstract', numbering=False)):
            doc.append(abstract)

        # Sections
        for sec in sections_data:
            with doc.create(Section(sec.get('title', 'Untitled'))):
                doc.append(sec.get('content', ''))

        # Save
        safe_title = "".join(c if c.isalnum() else "_" for c in title)[:50]
        filepath = Path(output_dir) / f"paper_{safe_title}"
        doc.generate_tex(str(filepath))
        result["latex_file"] = f"{filepath}.tex"

        # Try PDF compilation
        try:
            doc.generate_pdf(str(filepath), clean_tex=False)
            result["pdf_file"] = f"{filepath}.pdf"
        except:
            pass  # LaTeX not installed, that's okay

    except Exception as e:
        result["error"] = str(e)

    return json.dumps(result, indent=2)


# ============================================================================
# SYSTEM PROMPT
# ============================================================================

SYSTEM_PROMPT = """You are a self-evolving research agent. Your mission: conduct research autonomously.

**EVOLUTION CYCLE: PLAN → IMPLEMENT → VERIFY → SHIP**

## Principles

1. **Autonomy**: Need a package? Install it. Need a tool? Create it (write code).
2. **Minimalism**: Keep it simple and functional.
3. **Scientific**: Formulate hypotheses, run experiments, verify results, document findings.

## Available Tools

- `run_experiment(code, language)`: Execute Python or bash code
- `install_package(package_name)`: Install Python packages autonomously
- `draft_paper(title, authors, abstract, sections)`: Generate LaTeX papers

## Research Workflow

When given a research task:

1. **Plan**: Formulate hypotheses and experimental design
2. **Implement**: Write code, install packages as needed, run experiments
3. **Verify**: Test results, check validity
4. **Ship**: Draft paper with findings

If you need a capability not provided, CREATE IT by writing code.
Be bold. Be scientific. Evolve.
"""


# ============================================================================
# AGENT
# ============================================================================

class ResearchAgent:
    """
    Minimalist self-evolving research agent.
    """

    def __init__(
        self,
        model: Optional[Any] = None,
        model_id: str = "Qwen/Qwen2.5-Coder-32B-Instruct",
        hf_token: Optional[str] = None,
        verbose: bool = True
    ):
        """
        Initialize the ResearchAgent.

        Args:
            model: Pre-configured model (if None, creates HfApiModel)
            model_id: HuggingFace model ID
            hf_token: HF token (or set HF_TOKEN env var)
            verbose: Enable verbose output
        """
        if model is None:
            token = hf_token or os.getenv("HF_TOKEN")
            if not token:
                raise ValueError("Set HF_TOKEN env var or pass hf_token parameter")
            model = HfApiModel(model_id=model_id, token=token)

        # Create agent with minimal tools
        self.agent = CodeAgent(
            tools=[run_experiment, install_package, draft_paper],
            model=model,
            additional_authorized_imports=['numpy', 'pandas', 'scipy', 'sklearn', 'matplotlib'],
            add_base_tools=True,
            verbose=verbose,
            system_prompt=SYSTEM_PROMPT
        )
        self.verbose = verbose

    def research(self, task: str, **kwargs) -> Any:
        """
        Conduct research on a task.

        Args:
            task: Research task description

        Returns:
            Research results
        """
        if self.verbose:
            print(f"\n{'='*60}\n🔬 MSR-Scientist\n{'='*60}\n{task}\n{'='*60}\n")

        result = self.agent.run(task, **kwargs)

        if self.verbose:
            print(f"\n{'='*60}\n✅ Complete\n{'='*60}\n")

        return result

    def __call__(self, task: str, **kwargs) -> Any:
        return self.research(task, **kwargs)


# ============================================================================
# CONVENIENCE FUNCTION
# ============================================================================

def create_agent(
    model_id: str = "Qwen/Qwen2.5-Coder-32B-Instruct",
    hf_token: Optional[str] = None,
    **kwargs
) -> ResearchAgent:
    """Create a ResearchAgent with defaults."""
    return ResearchAgent(model_id=model_id, hf_token=hf_token, **kwargs)


# ============================================================================
# CLI (optional)
# ============================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python msr_scientist.py 'Your research task here'")
        sys.exit(1)

    task = " ".join(sys.argv[1:])
    agent = create_agent()
    agent.research(task)
