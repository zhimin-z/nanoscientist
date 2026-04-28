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
  → LiteratureReviewLoop   (autonomous loop: terminates on quality gate or budget)
  → ExperimentationLoop    (autonomous loop: terminates on quality gate or budget)
  → WritingLoop            (autonomous loop: terminates on quality gate or budget)
  → CompilingLoop (CompileTeX ↔ FixTeX)
  → Finisher
```
Each loop runs `_run_loop`: each iteration the LLM decides `action: skill|done`, the skill executes, then a quality gate checks if the stage goal is met. Loops exit on goal achieved, budget exhaustion, or max iterations.

## Architecture

| Node | Role |
|---|---|
| **Initializer** | Zero LLM calls — infers report type from budget, creates `outputs/<uuid>/` |
| **LiteratureReviewLoop** | Autonomous loop over literature skills (paper-navigator, research-survey, etc.); exits when literature goal met or budget low |
| **ExperimentationLoop** | Autonomous loop over experiment skills (experiment-pipeline, experiment-craft, etc.); exits when experiment goal met or budget low |
| **WritingLoop** | Writes all required sections, runs a writing review pass, addresses major comments; assembles final .tex |
| **CompilingLoop** | `pdflatex` + `bibtex`; FixTeX patches errors and recompiles (up to 2 fix attempts) |
| **Finisher** | Writes `cost_log.json` + `summary.json`, prints total cost |

## Key files
| File | Role |
|---|---|
| `src/nodes.py` | 7 nodes + helpers (`_run_loop`, `_decide_next_action`, `_execute_skill`, `_quality_gate`, `_write_section`, `_writing_review_pass`, `_assemble_tex`, `_build_context`, `_run_code_blocks`, `_save_artifact`) |
| `src/flow.py` | PocketFlow wiring |
| `src/utils.py` | LLM client (`call_llm_async`, `call_llm_with_tools_async`), tiktoken counter, cost tracking, BibTeX utils, skill index loading/filtering |
| `skills/skills.json` | Skill index (id + description) |

## Shared store keys
`topic`, `budget_dollars`, `budget_remaining`, `cost_log`, `skill_index`, `skills_dir`, `output_dir`, `output_path`, `history`, `artifacts`, `bibtex_entries`, `sections_written`, `section_bodies`, `tex_content`, `bib_content`, `fix_attempts`, `paper_title`, `figures_used`, `api_keys`

`history` entries: `{"step": int, "stage": "literature"|"experiment"|"writing"|"writing_revision", "label": str, "summary": str, "cost": float, "error": str|null}`

## Budget termination
All ratios are fractions of `budget_dollars` (original budget). Overridable via env vars. Defaults in `_DEFAULTS` in `src/nodes.py`.

| Env var | Default | Purpose |
|---|---|---|
| `BUDGET_RESERVE_RATIO` | 0.05 | stop loop if `remaining < budget * 0.05` |
| `WRITE_RESERVE_RATIO` | 0.02 | skip section write if below 2% of original budget |
| `REVIEW_RESERVE_RATIO` | 0.01 | skip writing review if below 1% of original budget |
| `MIN_CALLS_TO_CONTINUE` | 3 | stop loop if estimated remaining calls < this |

## Environment
Required: `OPENROUTER_API_KEY` (all nodes).
Optional (skill-gated): `HF_TOKEN`, `GITHUB_TOKEN`, `S2_API_KEY`.
Inference: `MODEL_NAME`, `INFERENCE_BASE_URL`, `INPUT_TOKEN_COST_PER_MILLION`, `OUTPUT_TOKEN_COST_PER_MILLION`.
Agent: `LOOKBACK` (default 3), `MAX_REVIEW_ROUNDS` (default 1), `MAX_TOOL_ROUNDS` (default 16), `MAX_LOOP_ITERATIONS` (default 20).
Tuning (all optional; nodes.py defaults in `_DEFAULTS`, utils.py defaults as module-level constants):
- Timeouts: `CODE_EXEC_TIMEOUT` (default 300s), `LATEX_COMPILE_TIMEOUT` (default 60s)
- Tool execution: `TOOL_DEFAULT_TIMEOUT` (default 60s), `TOOL_MAX_TIMEOUT` (default 300s), `TOOL_STDOUT_LIMIT` (default 4000 chars), `TOOL_STDERR_LIMIT` (default 1000 chars)
- Context windows: `SKILL_CONTENT_LIMIT`, `ARTIFACT_CONTEXT_CHARS`, `PRIOR_SECTION_CHARS`, `SALVAGE_CONTEXT_CHARS`, `TITLE_TOPIC_CHARS`
- Quality gates: `MIN_SECTION_LENGTH`, `TITLE_MAX_WORDS`
- Node retries/wait: `NODE_RETRIES` (default 2), `NODE_WAIT` (default 3)
- Cost estimation fallbacks: `EST_AVG_PROMPT_TOKENS` (default 500), `EST_AVG_OUTPUT_TOKENS` (default 300)

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
