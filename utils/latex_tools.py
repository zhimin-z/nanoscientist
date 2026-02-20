#!/usr/bin/env python3
"""
LaTeX repair and compilation utilities.

Extracted from the nanoscientist PocketFlow pipeline for use as a
standalone tool that skills can invoke via Bash.

Usage:
    python utils/latex_tools.py <paper_dir>
    python utils/latex_tools.py research_outputs/run-123/workspace/paper-writing/
"""
import re
import subprocess
import shutil
import sys
from pathlib import Path


def repair_latex(tex_content):
    """
    Auto-repair common LLM-generated LaTeX errors before compilation.
    Returns (repaired_content, list_of_repairs).
    """
    repairs = []

    # 1. Remove inputenc (XeTeX handles UTF-8 natively)
    new = re.sub(r'\\usepackage\[.*?\]\{inputenc\}\s*\n?', '', tex_content)
    if new != tex_content:
        repairs.append("removed inputenc (XeTeX native UTF-8)")
        tex_content = new

    # 2. Remove T1 fontenc (not needed with XeTeX)
    new = re.sub(r'\\usepackage\[T1\]\{fontenc\}\s*\n?', '', tex_content)
    if new != tex_content:
        repairs.append("removed T1 fontenc")
        tex_content = new

    # 3. Fix tabular column count mismatches
    tabular_re = re.compile(
        r'(\\begin\{tabular\}\{)([^}]+)(\})(.*?)(\\end\{tabular\})',
        re.DOTALL
    )

    def _fix_tabular(m):
        prefix, col_spec, brace, body, end = m.groups()
        spec_cols = len(re.findall(r'[lcrpmb]', col_spec))
        max_row_cols = 0
        for row in re.split(r'\\\\', body):
            stripped = row.strip()
            if not stripped or stripped.startswith('\\') and '&' not in stripped:
                continue
            n_cols = stripped.count('&') + 1
            max_row_cols = max(max_row_cols, n_cols)
        if max_row_cols > spec_cols:
            extra = max_row_cols - spec_cols
            last_type = re.findall(r'[lcrpmb]', col_spec)
            pad_char = last_type[-1] if last_type else 'r'
            new_spec = col_spec + pad_char * extra
            repairs.append(f"fixed tabular: {spec_cols} cols -> {max_row_cols} cols")
            return prefix + new_spec + brace + body + end
        return m.group(0)

    tex_content = tabular_re.sub(_fix_tabular, tex_content)

    # 4. Fix \includegraphics references (replace with placeholder if files missing)
    def _fix_includegraphics(m):
        repairs.append("replaced \\includegraphics with placeholder")
        return r'\rule{0.8\textwidth}{3cm}'

    tex_content = re.sub(
        r'\\includegraphics\[.*?\]\{.*?\}',
        _fix_includegraphics,
        tex_content
    )
    tex_content = re.sub(
        r'\\includegraphics\{.*?\}',
        _fix_includegraphics,
        tex_content
    )

    # 5. Fix unclosed environments
    env_re = re.compile(r'\\(begin|end)\{(\w+)\}')
    env_counts = {}
    for m in env_re.finditer(tex_content):
        action, env_name = m.group(1), m.group(2)
        if env_name == 'document':
            continue
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


def repair_bibtex(bib_content):
    """
    Remove truncated/incomplete BibTeX entries (unclosed braces).
    Returns (repaired_content, list_of_repairs).
    """
    repairs = []
    entries = []
    current_start = None
    depth = 0

    for i, c in enumerate(bib_content):
        if c == '@' and depth == 0:
            current_start = i
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0 and current_start is not None:
                entries.append(bib_content[current_start:i + 1])
                current_start = None

    if current_start is not None and depth > 0:
        truncated = bib_content[current_start:current_start + 80].split('\n')[0]
        repairs.append(f"removed truncated BibTeX entry: {truncated}...")

    if repairs:
        repaired = '\n\n'.join(entries) + '\n'
        return repaired, repairs

    return bib_content, repairs


def try_compile_latex(paper_dir):
    """
    Try to compile LaTeX with available tools (tectonic > pdflatex > Docker).
    Returns (success: bool, error_msg: str).
    """
    paper_dir = Path(paper_dir)
    pdf_file = paper_dir / "main.pdf"

    if pdf_file.exists():
        pdf_file.unlink()

    last_error = ""

    # 1. Try Tectonic
    tectonic_bin = shutil.which("tectonic") or str(Path.home() / ".local/bin/tectonic")
    if Path(tectonic_bin).exists():
        print("  Compiling LaTeX (tectonic)...")
        try:
            result = subprocess.run(
                [tectonic_bin, "main.tex"],
                cwd=paper_dir, capture_output=True, text=True, timeout=300
            )
            if pdf_file.exists():
                print("  PDF generated (tectonic)")
                return True, ""
            last_error = (result.stderr or result.stdout or "")[-2000:]
            print(f"  Tectonic failed: {last_error[-500:]}")
        except subprocess.TimeoutExpired:
            last_error = "Tectonic compilation timed out (>300s)"

    # 2. Try pdflatex
    if shutil.which("pdflatex"):
        print("  Compiling LaTeX (pdflatex)...")
        try:
            result = None
            for _ in range(2):
                result = subprocess.run(
                    ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
                    cwd=paper_dir, capture_output=True, text=True, timeout=120
                )
            if shutil.which("bibtex") and (paper_dir / "references.bib").exists():
                subprocess.run(["bibtex", "main"], cwd=paper_dir, capture_output=True, timeout=60)
                result = subprocess.run(
                    ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", "main.tex"],
                    cwd=paper_dir, capture_output=True, text=True, timeout=120
                )
            if pdf_file.exists():
                print("  PDF generated (pdflatex)")
                return True, ""
            if result:
                last_error = (result.stdout or result.stderr or "")[-2000:]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            if not last_error:
                last_error = "pdflatex timed out or not found"

    # 3. Try Docker
    if shutil.which("docker"):
        print("  Compiling LaTeX (Docker texlive)...")
        try:
            vol = str(paper_dir.resolve())
            cmd = (
                "pdflatex -interaction=nonstopmode -halt-on-error main.tex && "
                "bibtex main 2>/dev/null; "
                "pdflatex -interaction=nonstopmode -halt-on-error main.tex && "
                "pdflatex -interaction=nonstopmode -halt-on-error main.tex"
            )
            result = subprocess.run(
                ["docker", "run", "--rm", "-v", f"{vol}:/work", "-w", "/work",
                 "texlive/texlive:latest", "bash", "-c", cmd],
                capture_output=True, text=True, timeout=300
            )
            if pdf_file.exists():
                print("  PDF generated (Docker)")
                return True, ""
            last_error = (result.stdout or result.stderr or "")[-2000:]
        except (subprocess.TimeoutExpired, FileNotFoundError):
            if not last_error:
                last_error = "Docker compilation timed out or not found"

    return False, last_error


def compile_latex(paper_dir, max_repair_attempts=2):
    """
    Compile LaTeX to PDF with static error repair.

    Flow:
    1. Apply static repairs (repair_latex) - fast, deterministic
    2. Try compiling with available tools (tectonic > pdflatex > Docker)
    3. If fails, apply repairs again and retry
    """
    paper_dir = Path(paper_dir)
    tex_file = paper_dir / "main.tex"
    if not tex_file.exists():
        print(f"  No main.tex found in {paper_dir}")
        return False

    # Static LaTeX repairs
    tex_content = tex_file.read_text()
    patched, repairs = repair_latex(tex_content)
    if repairs:
        print(f"  Applied {len(repairs)} static LaTeX fix(es):")
        for r in repairs:
            print(f"    - {r}")
    if patched != tex_content:
        tex_file.write_text(patched)

    # Static BibTeX repair
    bib_file = paper_dir / "references.bib"
    if bib_file.exists():
        bib_content = bib_file.read_text()
        patched_bib, bib_repairs = repair_bibtex(bib_content)
        if bib_repairs:
            print(f"  Applied {len(bib_repairs)} static BibTeX fix(es):")
            for r in bib_repairs:
                print(f"    - {r}")
            bib_file.write_text(patched_bib)

    # Compile
    for attempt in range(max_repair_attempts + 1):
        success, error_msg = try_compile_latex(paper_dir)
        if success:
            if attempt > 0:
                print(f"  Compilation succeeded after {attempt} repair attempt(s)")
            return True

        if attempt < max_repair_attempts:
            # Re-read and re-apply static repairs
            current_tex = tex_file.read_text()
            fixed, extra_repairs = repair_latex(current_tex)
            if extra_repairs:
                tex_file.write_text(fixed)

    print("  Could not compile PDF after all repair attempts.")
    print("  Try: cd <paper_dir> && tectonic main.tex")
    return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python utils/latex_tools.py <paper_dir>")
        sys.exit(1)

    paper_dir = Path(sys.argv[1])
    if not paper_dir.exists():
        print(f"Directory not found: {paper_dir}")
        sys.exit(1)

    success = compile_latex(paper_dir)
    sys.exit(0 if success else 1)
