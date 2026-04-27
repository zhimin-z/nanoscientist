# CLAUDE.md

## Project
Nano-scientist autonomous research agent. Takes a topic + dollar budget, runs research/writing/review loops, outputs a compiled PDF.

## Run
```bash
python main.py "topic" --budget 1.00
python main.py --list-skills
```

## Pipeline
```
Initializer
  → PlanInitialExecutor
  → PlanDrivenExecutor (loop)
  → ReviewExecutor → [execute / compile]
  → CompileTeX ↔ FixTeX → Finisher
```
PlanInitialExecutor drafts a typed todo list (research + write steps).
PlanDrivenExecutor executes each step in order; optionally revises remaining plan after each step.
ReviewExecutor appends revision steps to the plan tail and loops back to PlanDrivenExecutor.
LaTeX compilation runs **exactly once**, as the final PDF generation step.

## Architecture

| Node | Role |
|---|---|
| **Initializer** | Zero LLM calls — infers report type from budget, creates `outputs/<uuid>/` |
| **PlanInitialExecutor** | One LLM call — drafts typed research+write steps into `shared["plan"]` |
| **PlanDrivenExecutor** | Loop: pops next pending step → executes (`_run_skill` or `_write_section`) → optionally revises remaining plan; exits to `review` when plan exhausted or budget low |
| **ReviewExecutor** | Assembles fresh draft, runs peer-review; appends revision steps to plan tail → loops to `execute`; returns `compile` when accepted; controlled by `MAX_REVIEW_ROUNDS` (default 1) |
| **CompileTeX** | `pdflatex` + `bibtex` pipeline; up to 2 fix attempts |
| **FixTeX** | Patches citation or LaTeX errors, retries compile |
| **Finisher** | Writes `cost_log.json` + `summary.json`, prints total cost |

## Key files
| File | Role |
|---|---|
| `src/nodes.py` | 7 nodes + module-level helpers (`_run_skill`, `_write_section`, `_assemble_tex`, `_artifact_index`, `_recent_history`, `_plan_context`, `_run_code_blocks`, `_save_artifact`) |
| `src/flow.py` | PocketFlow wiring |
| `src/utils.py` | LLM client (`call_llm`, `call_llm_with_tools`), tiktoken counter, cost tracking, BibTeX utils, skill index loading/filtering |
| `skills/skills.json` | Skill index (id + description) |

## Shared store keys
`topic`, `budget_dollars`, `budget_remaining`, `cost_log`, `skill_index`, `skills_dir`, `output_dir`, `output_path`, `report_type`, `history`, `plan`, `artifacts`, `bibtex_entries`, `sections_written`, `section_bodies`, `tex_content`, `bib_content`, `failed_code`, `review_rounds`, `review_comments`, `addressed_comments`, `fix_attempts`, `api_keys`

`plan` format: `[{"id": int, "type": "research"|"write", "task": str, "skill": str, "section": str (write steps only), "status": "pending"|"in_progress"|"done"|"failed"}]`

## Budget reserves
All values are overridable via env vars (same name). Defaults live in `_DEFAULTS` in `src/nodes.py`.

| Env var | Default | Purpose |
|---|---|---|
| `BUDGET_RESERVE` | $0.03 | research → writing threshold |
| `WRITE_RESERVE` | $0.015 | writing → review threshold |
| `REVIEW_RESERVE` | $0.008 | skip revision if below |

## Environment
Required: `OPENROUTER_API_KEY` (all nodes).
Optional (skill-gated): `HF_TOKEN`, `GITHUB_TOKEN`, `OPENAI_API_KEY`.
Inference: `MODEL_NAME`, `INFERENCE_BASE_URL`, `INPUT_TOKEN_COST_PER_MILLION`, `OUTPUT_TOKEN_COST_PER_MILLION`.
Agent: `LOOKBACK` (default 3), `MAX_REVIEW_ROUNDS` (default 1), `MAX_TOOL_ROUNDS` (default 16).
Tuning (all optional; nodes.py defaults in `_DEFAULTS`, utils.py defaults as module-level constants):
- Report thresholds: `BUDGET_QUICK_SUMMARY`, `BUDGET_LITERATURE_REVIEW`, `BUDGET_RESEARCH_REPORT`
- Timeouts: `CODE_EXEC_TIMEOUT` (default 300s), `LATEX_COMPILE_TIMEOUT` (default 60s)
- Tool execution: `TOOL_DEFAULT_TIMEOUT` (default 60s), `TOOL_MAX_TIMEOUT` (default 300s), `TOOL_STDOUT_LIMIT` (default 4000 chars), `TOOL_STDERR_LIMIT` (default 1000 chars)
- Plan: `PLAN_REVISE_EVERY` (revise plan every N research steps, default 3)
- Context windows: `SKILL_CONTENT_LIMIT`, `ARTIFACT_CONTEXT_CHARS`, `PRIOR_SECTION_CHARS`, `SALVAGE_CONTEXT_CHARS`, `TITLE_TOPIC_CHARS`
- Quality gates: `MIN_SECTION_LENGTH`, `TITLE_MAX_WORDS`
- Node retries/wait: `NODE_RETRIES` (default 2), `NODE_WAIT` (default 3)
- Tool execution: `TOOL_DEFAULT_TIMEOUT` (default 60s), `TOOL_MAX_TIMEOUT` (default 300s), `TOOL_STDOUT_LIMIT` (default 4000 chars), `TOOL_STDERR_LIMIT` (default 1000 chars)
- Step decomposition: `STEP_INSTRUCTION_MAX_WORDS` (default 30)
- Cost estimation fallbacks: `EST_AVG_PROMPT_TOKENS` (default 500), `EST_AVG_OUTPUT_TOKENS` (default 300)
- Workflow diagram: `WORKFLOW_IMAGE_SIZE` (default `1536x1024`), `WORKFLOW_IMAGE_QUALITY` (default `high`)

## Conventions
- Skills: `skills/<name>/SKILL.md` — lazy-loaded; index in `skills/skills.json`
- Skills with `allowed-tools: Bash` get a real tool-calling loop via `call_llm_with_tools`: the model drives bash execution, sees stdout/stderr, and can retry on error (up to 16 rounds; configurable via `MAX_TOOL_ROUNDS` env var)
- Skills without `allowed-tools` use plain `call_llm` (no tools exposed)
- BibTeX via `%%BEGIN BIBTEX%%...%%END BIBTEX%%`, deduplicated by `dedup_bibtex()`
- Sections via `%%BEGIN SECTION%%...%%END SECTION%%`
- `required-keys` frontmatter field declares which API keys a skill needs; skills are filtered out at startup if the key is missing
- Output: `outputs/<uuid>/` — do not commit

## Pasting trace output in chat
When pasting `python main.py` terminal traces into the chat, always wrap them in a code fence:
````
```
[trace output here]
```
````
This prevents the OMC keyword detector from false-triggering autopilot/ecomode modes on words like "autopilot", "research", etc. that appear in trace text.

## Adding a skill
1. `skills/<name>/SKILL.md` with YAML frontmatter:
   - `id`: skill name
   - `description`: shown in planner
   - `allowed-tools: Bash` — grants real bash tool access with error feedback loop
   - `required-keys: [KEY_NAME]` — skill filtered out at startup if key missing
2. `{"id": "<name>", "description": "..."}` in `skills/skills.json`
