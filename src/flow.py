"""Flow wiring for the Autonomous Scientist agent.

Pipeline:
  Initializer
    → ResearchExecutor (loop) → WritingExecutor (loop)
    → ReviewExecutor → [research / write / compile]
    → CompileTeX ↔ FixTeX → Finisher

Review dispatches revisions directly into the research/writing loops.
LaTeX compilation happens exactly once, as the final PDF generation step.
"""

from pocketflow import Flow

from .nodes import (
    Initializer,
    PlanExecutor,
    ResearchExecutor,
    WritingExecutor,
    ReviewExecutor,
    CompileTeX,
    FixTeX,
    Finisher,
)


def create_scientist_flow() -> Flow:
    init     = Initializer()
    planner  = PlanExecutor(max_retries=2, wait=3)
    research = ResearchExecutor(max_retries=2, wait=5)
    writing  = WritingExecutor(max_retries=2, wait=5)
    review   = ReviewExecutor(max_retries=2, wait=5)
    compile  = CompileTeX(max_retries=1, wait=0)
    fix_tex  = FixTeX(max_retries=2, wait=3)
    finisher = Finisher()

    # Entry: Initializer → PlanExecutor → ResearchExecutor
    init     - "research" >> planner
    planner  - "research" >> research

    # Research loop
    research - "research" >> research
    research - "write"    >> writing

    # Writing loop
    writing  - "write"    >> writing
    writing  - "review"   >> review

    # Review dispatches revisions directly or proceeds to compile
    review   - "research" >> research
    review   - "write"    >> writing
    review   - "compile"  >> compile

    # Final PDF generation (runs exactly once)
    compile  - "fix"      >> fix_tex
    compile  - "done"     >> finisher
    fix_tex  - "compile"  >> compile
    fix_tex  - "done"     >> finisher

    return Flow(start=init)
