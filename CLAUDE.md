# CLAUDE.md

## Project
Autonomous research agent. Takes a topic + dollar budget, runs research/writing/review loops, outputs a compiled PDF.

## Run
```bash
python main.py "topic" --budget 1.00
python main.py --list-skills
```

## Pipeline
```
Initializer
  → ResearchExecutor (loop)
  → WritingExecutor (loop)
  → ReviewExecutor → [research / write / compile]
  → CompileTeX ↔ FixTeX → Finisher
```
ReviewExecutor dispatches revisions directly into the research/writing loops.
LaTeX compilation runs **exactly once**, as the final PDF generation step.

## Architecture

| Node | Role |
|---|---|
| **Initializer** | Zero LLM calls — infers report type from budget, creates `outputs/<uuid>/` |
| **ResearchExecutor** | Autonomous loop: picks one skill → inline decompose (2–5 steps) → execute; self-loops until budget threshold; *scoped mode*: runs a single revision-directed skill then exits to `write` |
| **WritingExecutor** | Autonomous loop: picks one section → writes LaTeX; self-loops until all sections done; *scoped mode*: rewrites one targeted section then exits to `review` |
| **ReviewExecutor** | Assembles fresh draft, runs peer-review; dispatches top major comment → `research` / `write`; returns `compile` when accepted; controlled by `MAX_REVIEW_ROUNDS` (default 1) |
| **CompileTeX** | `pdflatex` + `bibtex` pipeline; up to 2 fix attempts |
| **FixTeX** | Patches citation or LaTeX errors, retries compile |
| **Finisher** | Writes `cost_log.json` + `summary.json`, prints total cost |

## Key files
| File | Role |
|---|---|
| `src/nodes.py` | 7 nodes + module-level helpers (`_run_skill`, `_write_section`, `_assemble_tex`, `_artifact_index`, `_recent_history`, `_run_code_blocks`, `_save_artifact`) |
| `src/flow.py` | PocketFlow wiring |
| `src/utils.py` | LLM client, tiktoken counter, cost tracking, BibTeX utils |
| `skills/skills.json` | Skill index (id + description) |
| `docs/PAPER_QUALITY_STANDARD.md` | Writing quality guide |

## Shared store keys
`topic`, `budget_dollars`, `budget_remaining`, `cost_log`, `skill_index`, `skills_dir`, `output_dir`, `output_path`, `report_type`, `history`, `artifacts`, `bibtex_entries`, `sections_written`, `section_bodies`, `tex_content`, `bib_content`, `failed_code`, `review_rounds`, `review_comments`, `addressed_comments`, `revision_scope`, `fix_attempts`, `api_keys`

## Budget reserves
| Constant | Value | Purpose |
|---|---|---|
| `BUDGET_RESERVE` | $0.03 | research → writing threshold |
| `WRITE_RESERVE` | $0.015 | writing → review threshold |
| `REVIEW_RESERVE` | $0.008 | skip revision if below |

## Environment
Required: `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, `HF_TOKEN`, `GITHUB_TOKEN`.
Inference: `MODEL_NAME`, `INFERENCE_BASE_URL`, `INPUT_TOKEN_COST_PER_MILLION`, `OUTPUT_TOKEN_COST_PER_MILLION`.
Agent: `LOOKBACK` (default 3), `MAX_REVIEW_ROUNDS` (default 1).

## Conventions
- Skills: `skills/<name>/SKILL.md` — lazy-loaded; index in `skills/skills.json`
- Code blocks (`%%BEGIN CODE:python%%...%%END CODE%%`) execute in task dir (requires `allowed-tools: Bash`)
- BibTeX via `%%BEGIN BIBTEX%%...%%END BIBTEX%%`, deduplicated by `dedup_bibtex()`
- Sections via `%%BEGIN SECTION%%...%%END SECTION%%`
- Output: `outputs/<uuid>/` — do not commit

## Adding a skill
1. `skills/<name>/SKILL.md` with YAML frontmatter (`id`, `description`, optionally `allowed-tools: Bash`)
2. `{"id": "<name>", "description": "..."}` in `skills/skills.json`
