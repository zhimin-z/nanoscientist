"""report_write.py - Stage 3: Report Generation module

Reads research_proposal.md and workspace/README.md to generate a PDF report
using the ACM Conference template. Converts [@ref] citations to LaTeX \cite{ref}.
"""

import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

import litellm
from config import get_config, ModelConfig

# Path to ACM template relative to project root
ACM_TEMPLATE_ZIP = Path(__file__).parent.parent / "research_template" / "ACM_Conference_Proceedings_Primary_Article_Template.zip"
ACM_TEMPLATE_DIR_NAME = "ACM_Conference_Proceedings_Primary_Article_Template"


def parse_proposal_for_report(proposal_path: Path) -> Dict:
    """Parse research_proposal.md for report generation.

    Args:
        proposal_path: Path to research_proposal.md

    Returns:
        Dictionary with parsed data including citations
    """
    content = proposal_path.read_text(encoding='utf-8')

    # Extract metadata from frontmatter
    metadata = {}
    frontmatter_match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
    if frontmatter_match:
        for line in frontmatter_match.group(1).split('\n'):
            if ':' in line:
                key, value = line.split(':', 1)
                metadata[key.strip()] = value.strip()

    # Extract title
    title_match = re.search(r'^# Research Proposal: (.+)$', content, re.MULTILINE)
    task = title_match.group(1) if title_match else "Research Report"

    # Extract hypotheses
    hypotheses = []
    hypo_section = re.search(r'## Hypotheses\n+(.*?)(?=\n##|\n---|\Z)', content, re.DOTALL)
    if hypo_section:
        hypotheses = re.findall(r'^\d+\.\s+(.+)$', hypo_section.group(1), re.MULTILINE)

    # Extract methodology
    methodology = {}
    method_section = re.search(r'## Methodology\n+(.*?)(?=\n##|\n---|\Z)', content, re.DOTALL)
    if method_section:
        method_content = method_section.group(1)
        approach_match = re.search(r'\*\*Approach:\*\*\s*(.+?)(?=\n\*\*|\n\n|\Z)', method_content, re.DOTALL)
        if approach_match:
            methodology["approach"] = approach_match.group(1).strip()

    # Extract citations - parse the Citations section
    citations = []
    citations_section = re.search(r'## Citations\n+(.*?)(?=\n---|\Z)', content, re.DOTALL)
    if citations_section:
        # Pattern: - **ref1**: Author (Year). "Title". *Publisher*. URL
        citation_pattern = r'-\s+\*\*(\w+)\*\*:\s*([^(]+)\s*\((\d{4}|n\.d\.)\)\.\s*"([^"]+)"\.?\s*(?:\*([^*]+)\*\.?)?\s*(https?://\S+)?'
        for match in re.finditer(citation_pattern, citations_section.group(1)):
            citations.append({
                "bibtex_key": match.group(1),
                "authors": match.group(2).strip(),
                "year": match.group(3),
                "title": match.group(4),
                "publisher": match.group(5) or "",
                "url": match.group(6) or "",
                "type": "misc"
            })

    # Extract background context
    background = ""
    bg_section = re.search(r'## Background Context\n+(.*?)(?=\n---|\Z)', content, re.DOTALL)
    if bg_section:
        background = bg_section.group(1).strip()

    return {
        "metadata": metadata,
        "task": task,
        "hypotheses": hypotheses,
        "methodology": methodology,
        "citations": citations,
        "background": background,
        "full_content": content
    }


def parse_workspace_readme(readme_path: Path) -> Dict:
    """Parse workspace README.md for artifact information.

    Args:
        readme_path: Path to workspace/README.md

    Returns:
        Dictionary with artifact information
    """
    if not readme_path.exists():
        return {"scripts": [], "figures": [], "data_files": []}

    content = readme_path.read_text(encoding='utf-8')

    # Extract scripts
    scripts = re.findall(r'^-\s+`([^`]+\.py)`', content, re.MULTILINE)

    # Extract figures
    figures = re.findall(r'^-\s+`([^`]+\.(?:png|jpg))`', content, re.MULTILINE)

    # Extract data files
    data_files = re.findall(r'^-\s+`([^`]+\.(?:json|csv|txt))`', content, re.MULTILINE)

    return {
        "scripts": scripts,
        "figures": figures,
        "data_files": data_files
    }


def convert_markdown_citations(text: str) -> str:
    """Convert [@ref] citations to LaTeX \\cite{ref} format.

    Args:
        text: Text with markdown citations

    Returns:
        Text with LaTeX citations
    """
    # Convert [@ref1; @ref2] to \cite{ref1,ref2}
    def multi_cite(match):
        refs = re.findall(r'@(\w+)', match.group(0))
        return f"\\cite{{{','.join(refs)}}}"

    text = re.sub(r'\[@[^]]+\]', multi_cite, text)

    # Convert single [@ref] that might have been missed
    text = re.sub(r'\[@(\w+)\]', r'\\cite{\1}', text)

    return text


def _escape_latex(text: str) -> str:
    """Escape special LaTeX characters, preserving citation commands.

    Args:
        text: Raw text to escape

    Returns:
        LaTeX-safe text
    """
    # First convert markdown citations to LaTeX
    text = convert_markdown_citations(text)

    # Protect citation commands
    citations = []
    def save_citation(match):
        citations.append(match.group(0))
        return f"__CITATION_{len(citations)-1}__"

    text = re.sub(r'\\cite[tp]?\{[^}]+\}', save_citation, text)

    replacements = {
        '&': r'\&',
        '%': r'\%',
        '$': r'\$',
        '#': r'\#',
        '_': r'\_',
        '{': r'\{',
        '}': r'\}',
        '~': r'\textasciitilde{}',
        '^': r'\textasciicircum{}',
    }

    result = text
    for char, replacement in replacements.items():
        result = result.replace(char, replacement)

    # Restore citation commands
    for i, citation in enumerate(citations):
        result = result.replace(f"__CITATION_{i}__", citation)

    return result


def _generate_bibtex_entry(citation: Dict) -> str:
    """Generate a BibTeX entry from citation metadata."""
    bibtex_key = citation.get("bibtex_key", "ref")
    entry_type = citation.get("type", "misc")

    lines = [f"@{entry_type}{{{bibtex_key},"]

    title = citation.get("title", "Unknown Title")
    lines.append(f"  title = {{{title}}},")

    authors = citation.get("authors", "Unknown")
    if authors and authors != "Unknown":
        lines.append(f"  author = {{{authors}}},")

    year = citation.get("year", "n.d.")
    if year and year != "n.d.":
        lines.append(f"  year = {{{year}}},")

    publisher = citation.get("publisher", "")
    if publisher:
        lines.append(f"  publisher = {{{publisher}}},")

    url = citation.get("url", "")
    if url:
        lines.append(f"  url = {{{url}}},")

    if lines[-1].endswith(","):
        lines[-1] = lines[-1][:-1]
    lines.append("}")

    return "\n".join(lines)


def _generate_bibtex_file(citations: List[Dict], output_path: Path) -> str:
    """Generate reference.bib from citations."""
    bib_content = "% BibTeX bibliography generated by Mini-Researcher-Agent\n\n"

    for citation in citations:
        bib_content += _generate_bibtex_entry(citation) + "\n\n"

    bib_file = output_path / "reference.bib"
    bib_file.write_text(bib_content, encoding='utf-8')

    return str(bib_file)


def _synthesize_paper_content(
    proposal: Dict,
    artifacts: Dict,
    workspace_path: Path,
    model_id: str
) -> Dict:
    """Use LLM to synthesize paper content from proposal and artifacts.

    Args:
        proposal: Parsed proposal dictionary
        artifacts: Parsed workspace artifacts
        workspace_path: Path to workspace for figure paths
        model_id: LLM model to use

    Returns:
        Dictionary with paper structure
    """
    config = get_config()

    if model_id:
        temp_config = ModelConfig(model_id=model_id)
        normalized_model = temp_config.normalize_model_id()
    else:
        normalized_model = config.stage3_model.normalize_model_id()

    # Build citation reference for prompt
    citation_keys = [c["bibtex_key"] for c in proposal.get("citations", [])]

    prompt = f"""Synthesize an academic research paper from this data:

RESEARCH TASK:
{proposal.get('task', 'Research Report')}

HYPOTHESES:
{chr(10).join(f"- {h}" for h in proposal.get('hypotheses', []))}

METHODOLOGY:
{proposal.get('methodology', {}).get('approach', 'See proposal for details.')}

BACKGROUND CONTEXT:
{proposal.get('background', '')[:3000]}

AVAILABLE CITATIONS (use these keys with \\cite{{key}}):
{', '.join(citation_keys)}

ARTIFACTS GENERATED:
- Scripts: {', '.join(artifacts.get('scripts', []))}
- Figures: {', '.join(artifacts.get('figures', []))}
- Data files: {', '.join(artifacts.get('data_files', []))}

Generate a JSON structure for an ACM-style research paper. Use \\cite{{refX}} for citations.
Include these sections: Abstract, Introduction, Background, Methodology, Results, Implications, Threats to Validity, Conclusion.

Required JSON structure:
{{
  "title": "Concise paper title",
  "authors": "AI Researcher",
  "abstract": "Abstract with citations \\cite{{ref1}}",
  "sections": [
    {{"title": "Introduction", "content": "..."}},
    {{"title": "Background", "content": "..."}},
    {{"title": "Methodology", "content": "..."}},
    {{"title": "Results", "content": "..."}},
    {{"title": "Implications", "content": "..."}},
    {{"title": "Threats to Validity", "content": "..."}},
    {{"title": "Conclusion", "content": "..."}}
  ]
}}

Return ONLY valid JSON."""

    response = litellm.completion(
        model=normalized_model,
        messages=[{'role': 'user', 'content': prompt}],
        response_format={"type": "json_object"},
        temperature=config.stage3_model.temperature
    )

    content = response.choices[0].message.content

    try:
        import json
        paper = json.loads(content)
    except Exception:
        # Fallback
        paper = {
            "title": proposal.get('task', 'Research Report'),
            "authors": "AI Researcher",
            "abstract": f"This research explores {proposal.get('task', 'the given topic')}.",
            "sections": [
                {"title": "Introduction", "content": proposal.get('background', '')[:500]},
                {"title": "Methodology", "content": proposal.get('methodology', {}).get('approach', 'N/A')},
                {"title": "Results", "content": "See workspace artifacts for detailed results."},
                {"title": "Conclusion", "content": "Summary of findings."}
            ]
        }

    return paper


def _generate_acm_latex(
    paper_content: Dict,
    citations: List[Dict],
    figures: List[str],
    workspace_path: Path
) -> str:
    """Generate ACM-formatted LaTeX content.

    Args:
        paper_content: Paper structure dictionary
        citations: List of citation dictionaries
        figures: List of figure filenames
        workspace_path: Path to workspace for figure paths

    Returns:
        LaTeX source code
    """
    title = _escape_latex(paper_content.get("title", "Research Report"))
    authors = paper_content.get("authors", "AI Researcher")
    abstract = _escape_latex(paper_content.get("abstract", ""))

    latex = r"""\documentclass[sigconf]{acmart}

\AtBeginDocument{%
  \providecommand\BibTeX{{%
    Bib\TeX}}}

\setcopyright{none}
\acmConference[Research Report]{Mini-Researcher-Agent Generated Report}{}{}

\begin{document}

\title{""" + title + r"""}

"""

    # Add author(s)
    author_list = [a.strip() for a in authors.split(",")]
    for author in author_list:
        escaped_author = _escape_latex(author)
        latex += f"\\author{{{escaped_author}}}\n"
        latex += "\\affiliation{%\n  \\institution{Research Institution}\n  \\country{USA}}\n"
        latex += f"\\email{{{escaped_author.lower().replace(' ', '.')}@research.org}}\n\n"

    latex += r"""
\begin{abstract}
""" + abstract + r"""
\end{abstract}

\maketitle

"""

    # Add sections
    sections = paper_content.get("sections", [])
    for sec in sections:
        section_title = _escape_latex(sec.get("title", "Untitled"))
        section_content = _escape_latex(sec.get("content", ""))

        latex += f"\\section{{{section_title}}}\n\n"
        latex += section_content + "\n\n"

        # Add figures in Results section
        if sec.get("title") == "Results" and figures:
            for fig_name in figures:
                fig_stem = Path(fig_name).stem
                caption = _escape_latex(f"Figure: {fig_stem}")
                latex += f"""
\\begin{{figure}}[h]
  \\centering
  \\includegraphics[width=\\linewidth]{{{fig_name}}}
  \\caption{{{caption}}}
  \\Description{{{caption}}}
\\end{{figure}}

"""

    latex += r"""
\begin{acks}
This research report was generated by Mini-Researcher-Agent.
\end{acks}

\bibliographystyle{ACM-Reference-Format}
\bibliography{reference}

\end{document}
"""

    return latex


def create_pdf_report(
    proposal_file: str,
    workspace_path: str,
    output_dir: str,
    model_id: str = "anthropic/claude-haiku-4-5-20251001"
) -> str:
    """Generate a PDF research report from proposal and workspace.

    Args:
        proposal_file: Path to research_proposal.md
        workspace_path: Path to workspace directory
        output_dir: Directory for PDF output
        model_id: LLM model for paper synthesis

    Returns:
        Path to generated PDF file
    """
    proposal_path = Path(proposal_file)
    workspace = Path(workspace_path)
    output_path = Path(output_dir)

    # Parse inputs
    proposal = parse_proposal_for_report(proposal_path)
    readme_path = workspace / "README.md"
    artifacts = parse_workspace_readme(readme_path)

    # Synthesize paper content
    paper_content = _synthesize_paper_content(proposal, artifacts, workspace, model_id)

    # Setup ACM template
    if not ACM_TEMPLATE_ZIP.exists():
        raise FileNotFoundError(f"ACM template not found at: {ACM_TEMPLATE_ZIP}")

    output_path.mkdir(parents=True, exist_ok=True)

    # Create report subdirectory
    report_dir = output_path / "report"
    report_dir.mkdir(parents=True, exist_ok=True)

    # Copy and extract ACM template
    dest_zip = report_dir / ACM_TEMPLATE_ZIP.name
    shutil.copy2(ACM_TEMPLATE_ZIP, dest_zip)
    print(f"✓ Copied ACM template")

    with zipfile.ZipFile(dest_zip, 'r') as zip_ref:
        zip_ref.extractall(report_dir)
    print(f"✓ Extracted ACM template")

    dest_zip.unlink()

    template_dir = report_dir / ACM_TEMPLATE_DIR_NAME

    # Generate reference.bib
    citations = proposal.get("citations", [])
    if citations:
        bib_file = _generate_bibtex_file(citations, template_dir)
        print(f"✓ Generated {bib_file}")

    # Copy figures from workspace
    figures = artifacts.get("figures", [])
    for fig_name in figures:
        src_fig = workspace / fig_name
        if src_fig.exists():
            shutil.copy2(src_fig, template_dir / fig_name)
            print(f"✓ Copied figure: {fig_name}")

    # Generate main.tex
    latex_content = _generate_acm_latex(paper_content, citations, figures, workspace)
    main_tex = template_dir / "main.tex"
    main_tex.write_text(latex_content, encoding='utf-8')
    print(f"✓ Generated main.tex")

    # Compile with Tectonic
    pdf_file = template_dir / "main.pdf"

    try:
        env = os.environ.copy()
        local_bin = str(Path.home() / ".local" / "bin")
        if local_bin not in env.get("PATH", ""):
            env["PATH"] = f"{local_bin}:{env.get('PATH', '')}"

        subprocess.run(
            ['tectonic', 'main.tex'],
            cwd=template_dir,
            env=env,
            capture_output=True,
            text=True,
            check=True
        )

        if pdf_file.exists():
            print(f"✓ PDF generated: {pdf_file}")
            return str(pdf_file)
        else:
            raise RuntimeError("Tectonic completed but PDF not found")

    except subprocess.CalledProcessError as e:
        print(f"Warning: Tectonic compilation failed:\n{e.stderr}")
        print(f"LaTeX source saved to: {main_tex}")
        return str(main_tex)
    except FileNotFoundError:
        print("Warning: Tectonic not found. Install with: pip install tectonic")
        print(f"LaTeX source saved to: {main_tex}")
        return str(main_tex)


def validate_report(pdf_path: str) -> tuple[bool, str]:
    """Validate generated PDF report."""
    pdf_file = Path(pdf_path)

    if not pdf_file.exists():
        return False, "PDF file does not exist"

    if pdf_file.suffix == '.pdf' and pdf_file.stat().st_size < 1024:
        return False, "PDF file is too small (likely empty)"

    return True, "Valid"


# Example usage
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 4:
        print("Usage: python report_write.py <proposal_file> <workspace_path> <output_dir>")
        sys.exit(1)

    proposal_file = sys.argv[1]
    workspace_path = sys.argv[2]
    output_dir = sys.argv[3]

    print(f"Generating PDF report...")
    print(f"  Proposal: {proposal_file}")
    print(f"  Workspace: {workspace_path}")
    print(f"  Output: {output_dir}")

    pdf_path = create_pdf_report(proposal_file, workspace_path, output_dir)

    print(f"\n{'='*60}")
    print(f"✓ PDF generated: {pdf_path}")
    print(f"{'='*60}\n")

    is_valid, msg = validate_report(pdf_path)
    if is_valid:
        print("✓ Report validated successfully")
    else:
        print(f"✗ Validation failed: {msg}")
