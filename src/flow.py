"""Flow wiring for the Autonomous Scientist agent."""

from pocketflow import Flow

from .nodes import (
    BudgetPlanner,
    DecideNext,
    ExecuteSkill,
    GenerateFigures,
    GenerateTables,
    WriteTeX,
    CompileTeX,
    FixTeX,
    QualityReview,
    Finisher,
)


def create_scientist_flow() -> Flow:
    """Create and wire the autonomous scientist flow.

    Flow diagram:
        BudgetPlanner → DecideNext ↔ ExecuteSkill (loop)
                         ↓
                       GenerateFigures → GenerateTables → WriteTeX
                                                            ↓
                                                    CompileTeX ↔ FixTeX (loop)
                                                        ↓
                                                  QualityReview
                                                   ↓          ↓
                                                 (deepen)    (done)
                                                   ↓          ↓
                                                DecideNext  Finisher
    """
    planner         = BudgetPlanner(max_retries=5, wait=3)
    decide          = DecideNext(max_retries=2, wait=3)
    execute_skill   = ExecuteSkill(max_retries=2, wait=5)
    generate_figs   = GenerateFigures(max_retries=1, wait=3)
    generate_tabs   = GenerateTables(max_retries=1, wait=3)
    write_tex       = WriteTeX(max_retries=2, wait=3)
    compile_tex     = CompileTeX(max_retries=1, wait=0)
    fix_tex         = FixTeX(max_retries=2, wait=3)
    quality_review  = QualityReview(max_retries=5, wait=3)
    finisher        = Finisher()

    # Agent loop: collect data via skills
    planner - "execute"       >> decide
    decide  - "execute_skill" >> execute_skill
    decide  - "write_tex"     >> generate_figs
    execute_skill - "decide"  >> decide

    # Post-collection: generate visuals from ALL collected data/artifacts
    generate_figs - "write"   >> generate_tabs
    generate_tabs - "write"   >> write_tex

    # Writing → compilation
    write_tex     - "compile" >> compile_tex

    # Compile & fix loop
    compile_tex - "fix"     >> fix_tex
    compile_tex - "done"    >> quality_review
    fix_tex     - "compile" >> compile_tex
    fix_tex     - "done"    >> quality_review

    # Quality gate: if budget remains, identify gaps and loop back
    quality_review - "deepen" >> decide
    quality_review - "done"   >> finisher

    return Flow(start=planner)
