"""LaTeX paper generation and compilation using pylatex."""

from typing import List, Dict, Any, Optional
from pathlib import Path
import subprocess


class LatexPaperWriter:
    """Generate and compile research papers in LaTeX."""

    def __init__(self, workspace: str = "."):
        """Initialize paper writer.

        Args:
            workspace: Directory for paper files
        """
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)

    def generate_paper(
        self,
        title: str,
        authors: List[str],
        abstract: str,
        sections: List[Dict[str, Any]],
        filename: str = "paper.tex"
    ) -> str:
        """Generate a LaTeX paper.

        Args:
            title: Paper title
            authors: List of author names
            abstract: Abstract text
            sections: List of sections with 'title' and 'content' keys
            filename: Output filename

        Returns:
            Path to generated .tex file
        """
        try:
            from pylatex import Document, Section, Subsection, Command
            from pylatex.utils import NoEscape

            # Create document
            doc = Document(documentclass="article")

            # Preamble
            doc.preamble.append(Command("title", title))
            doc.preamble.append(Command("author", NoEscape(" \\and ".join(authors))))
            doc.preamble.append(Command("date", Command("today")))

            # Begin document
            doc.append(NoEscape(r"\maketitle"))

            # Abstract
            doc.append(NoEscape(r"\begin{abstract}"))
            doc.append(abstract)
            doc.append(NoEscape(r"\end{abstract}"))

            # Sections
            for sec in sections:
                with doc.create(Section(sec["title"])):
                    content = sec.get("content", "")
                    doc.append(NoEscape(content))

                    # Handle subsections if any
                    for subsec in sec.get("subsections", []):
                        with doc.create(Subsection(subsec["title"])):
                            doc.append(NoEscape(subsec.get("content", "")))

            # Generate .tex file
            filepath = self.workspace / filename
            doc.generate_tex(str(filepath.with_suffix("")))

            return str(filepath)

        except ImportError:
            # Fallback: generate plain LaTeX without pylatex
            return self._generate_plain_latex(title, authors, abstract, sections, filename)

    def _generate_plain_latex(
        self,
        title: str,
        authors: List[str],
        abstract: str,
        sections: List[Dict[str, Any]],
        filename: str
    ) -> str:
        """Generate LaTeX paper without pylatex (fallback).

        Args:
            title: Paper title
            authors: List of author names
            abstract: Abstract text
            sections: List of sections
            filename: Output filename

        Returns:
            Path to generated .tex file
        """
        latex_content = r"""\documentclass{article}
\usepackage[utf8]{inputenc}
\usepackage{amsmath}
\usepackage{graphicx}
\usepackage{hyperref}

"""
        latex_content += f"\\title{{{title}}}\n"
        author_str = ' \\and '.join(authors)
        latex_content += f"\\author{{{author_str}}}\n"
        latex_content += r"\date{\today}" + "\n\n"
        latex_content += r"\begin{document}" + "\n\n"
        latex_content += r"\maketitle" + "\n\n"
        latex_content += r"\begin{abstract}" + "\n"
        latex_content += abstract + "\n"
        latex_content += r"\end{abstract}" + "\n\n"

        for sec in sections:
            latex_content += f"\\section{{{sec['title']}}}\n"
            latex_content += sec.get("content", "") + "\n\n"

            for subsec in sec.get("subsections", []):
                latex_content += f"\\subsection{{{subsec['title']}}}\n"
                latex_content += subsec.get("content", "") + "\n\n"

        latex_content += r"\end{document}"

        filepath = self.workspace / filename
        with open(filepath, "w") as f:
            f.write(latex_content)

        return str(filepath)

    def compile_pdf(self, tex_file: str, cleanup: bool = True) -> Optional[str]:
        """Compile LaTeX to PDF.

        Args:
            tex_file: Path to .tex file
            cleanup: Remove auxiliary files after compilation

        Returns:
            Path to generated PDF, or None if compilation failed
        """
        tex_path = Path(tex_file)
        if not tex_path.exists():
            raise FileNotFoundError(f"TeX file not found: {tex_file}")

        try:
            # Run pdflatex
            result = subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", tex_path.name],
                cwd=tex_path.parent,
                capture_output=True,
                text=True,
                timeout=60
            )

            # Check if PDF was generated
            pdf_path = tex_path.with_suffix(".pdf")
            if pdf_path.exists():
                # Clean up auxiliary files
                if cleanup:
                    for ext in [".aux", ".log", ".out"]:
                        aux_file = tex_path.with_suffix(ext)
                        if aux_file.exists():
                            aux_file.unlink()

                return str(pdf_path)
            else:
                print(f"PDF compilation failed:\n{result.stderr}")
                return None

        except subprocess.TimeoutExpired:
            print("PDF compilation timed out")
            return None
        except FileNotFoundError:
            print("pdflatex not found. Install: apt-get install texlive-latex-base")
            return None
        except Exception as e:
            print(f"PDF compilation error: {e}")
            return None

    def create_quick_paper(
        self,
        title: str,
        content: str,
        filename: str = "paper.tex",
        compile: bool = True
    ) -> Dict[str, Optional[str]]:
        """Quickly create a paper from markdown-like content.

        Args:
            title: Paper title
            content: Paper content (markdown-like)
            filename: Output filename
            compile: Whether to compile to PDF

        Returns:
            Dict with 'tex' and 'pdf' paths
        """
        # Parse sections from content (simple parsing)
        sections = []
        current_section = None

        for line in content.split("\n"):
            if line.startswith("# "):
                if current_section:
                    sections.append(current_section)
                current_section = {"title": line[2:], "content": ""}
            elif current_section:
                current_section["content"] += line + "\n"

        if current_section:
            sections.append(current_section)

        # Generate paper
        tex_file = self.generate_paper(
            title=title,
            authors=["MSR-Scientist Agent"],
            abstract="Generated by MSR-Scientist self-evolving research agent.",
            sections=sections,
            filename=filename
        )

        result = {"tex": tex_file, "pdf": None}

        # Compile if requested
        if compile:
            result["pdf"] = self.compile_pdf(tex_file)

        return result
