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
  → PlanExecutor
  → ResearchExecutor (loop)
  → WritingExecutor (loop)
  → ReviewExecutor → [research / write / compile]
  → CompileTeX ↔ FixTeX → Finisher
```
PlanExecutor drafts a structured todo list (feedforward control); ResearchExecutor marks items done and refines it (feedback control).
ReviewExecutor dispatches revisions directly into the research/writing loops.
LaTeX compilation runs **exactly once**, as the final PDF generation step.

## Architecture

| Node | Role |
|---|---|
| **Initializer** | Zero LLM calls — infers report type from budget, creates `outputs/<uuid>/` |
| **PlanExecutor** | One LLM call — drafts a 3–7 step ordered research plan into `shared["plan"]`; falls back to free-choice if parse fails |
| **ResearchExecutor** | Autonomous loop: follows plan → picks one skill → inline decompose (2–5 steps) → execute; marks plan items done; self-loops until budget threshold; *scoped mode*: runs a single revision-directed skill then exits to `write` |
| **WritingExecutor** | Autonomous loop: picks one section → writes LaTeX; self-loops until all sections done; *scoped mode*: rewrites one targeted section then exits to `review` |
| **ReviewExecutor** | Assembles fresh draft, runs peer-review; dispatches top major comment → `research` / `write`; returns `compile` when accepted; controlled by `MAX_REVIEW_ROUNDS` (default 1) |
| **CompileTeX** | `pdflatex` + `bibtex` pipeline; up to 2 fix attempts |
| **FixTeX** | Patches citation or LaTeX errors, retries compile |
| **Finisher** | Writes `cost_log.json` + `summary.json`, prints total cost |

## Key files
| File | Role |
|---|---|
| `src/nodes.py` | 8 nodes + module-level helpers (`_run_skill`, `_write_section`, `_assemble_tex`, `_artifact_index`, `_recent_history`, `_plan_context`, `_run_code_blocks`, `_save_artifact`) |
| `src/flow.py` | PocketFlow wiring |
| `src/utils.py` | LLM client (`call_llm`, `call_llm_with_tools`), tiktoken counter, cost tracking, BibTeX utils, skill index loading/filtering |
| `skills/skills.json` | Skill index (id + description) |
| `docs/PAPER_QUALITY_STANDARD.md` | Writing quality guide |

## Shared store keys
`topic`, `budget_dollars`, `budget_remaining`, `cost_log`, `skill_index`, `skills_dir`, `output_dir`, `output_path`, `report_type`, `history`, `plan`, `artifacts`, `bibtex_entries`, `sections_written`, `section_bodies`, `tex_content`, `bib_content`, `failed_code`, `review_rounds`, `review_comments`, `addressed_comments`, `revision_scope`, `fix_attempts`, `api_keys`

`plan` format: `[{"id": int, "task": str, "skill": str, "status": "pending"|"in_progress"|"done"}]`

## Budget reserves
| Constant | Value | Purpose |
|---|---|---|
| `BUDGET_RESERVE` | $0.03 | research → writing threshold |
| `WRITE_RESERVE` | $0.015 | writing → review threshold |
| `REVIEW_RESERVE` | $0.008 | skip revision if below |

## Environment
Required: `OPENROUTER_API_KEY` (all nodes).
Optional (skill-gated): `HF_TOKEN` (`tooluniverse`), `GITHUB_TOKEN` (`github-mining`), `OPENAI_API_KEY` (`paper-2-web`).
Inference: `MODEL_NAME`, `INFERENCE_BASE_URL`, `INPUT_TOKEN_COST_PER_MILLION`, `OUTPUT_TOKEN_COST_PER_MILLION`.
Agent: `LOOKBACK` (default 3), `MAX_REVIEW_ROUNDS` (default 1).

## Conventions
- Skills: `skills/<name>/SKILL.md` — lazy-loaded; index in `skills/skills.json`
- Skills with `allowed-tools: Bash` get a real tool-calling loop via `call_llm_with_tools`: the model drives bash execution, sees stdout/stderr, and can retry on error (up to 8 rounds)
- Skills without `allowed-tools` use plain `call_llm` (no tools exposed)
- BibTeX via `%%BEGIN BIBTEX%%...%%END BIBTEX%%`, deduplicated by `dedup_bibtex()`
- Sections via `%%BEGIN SECTION%%...%%END SECTION%%`
- `required-keys` frontmatter field declares which API keys a skill needs; skills are filtered out at startup if the key is missing
- Output: `outputs/<uuid>/` — do not commit

## Adding a skill
1. `skills/<name>/SKILL.md` with YAML frontmatter:
   - `id`: skill name
   - `description`: shown in planner
   - `allowed-tools: Bash` — grants real bash tool access with error feedback loop
   - `required-keys: [KEY_NAME]` — skill filtered out at startup if key missing
2. `{"id": "<name>", "description": "..."}` in `skills/skills.json`
