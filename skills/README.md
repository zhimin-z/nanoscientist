# Skills

This directory contains the **skill library** for Nano-scientist. Each subdirectory is a self-contained skill that the agent can invoke during a research run.

## How It Works

Skills are lazy-loaded at runtime. The agent reads `skills/skills.json` at startup for routing (id + description only), then loads the full `SKILL.md` body only when a skill is selected for execution.

Skills with `allowed-tools: Bash` get a real bash tool-calling loop — the model drives shell execution, sees stdout/stderr, and retries on error (up to `MAX_TOOL_ROUNDS`, default 16).

Skills without `allowed-tools` use a plain LLM call with no tool access.

## Available Skills

| Skill | Loop | Description |
| ----- | ---- | ----------- |
| [`paper-navigator`](paper-navigator/) | Literature | Find and read academic papers: keyword search, citation traversal, arXiv monitoring, SOTA lookup |
| [`research-survey`](research-survey/) | Literature | Structured literature survey reports: outline, draft, section expansion, final assembly |
| [`research-ideation`](research-ideation/) | Literature | Literature grounding, multi-persona idea generation, ELO ranking, proposal expansion |
| [`evo-memory`](evo-memory/) | Literature / Experiment | Persistent research memory: Ideation Memory and Experimentation Memory via IDE/IVE/ESE evolution |
| [`paper-planning`](paper-planning/) | Literature | Pre-writing paper planning: story design, experiment planning, figure design, 4-week timeline |
| [`experiment-pipeline`](experiment-pipeline/) | Experiment | Structured 4-stage execution: baseline, hyperparameter tuning, proposed method, ablation study |
| [`experiment-craft`](experiment-craft/) | Experiment | Experiment debugging: 5-step diagnostic flow, structured experiment logging |
| [`experiment-iterative-coder`](experiment-iterative-coder/) | Experiment | Iterative code refinement via plan→code→evaluate→refine cycles with lint/test scoring |
| [`paper-writing`](paper-writing/) | Writing | Academic paper sections: 11-step workflow with LaTeX templates and section guidance |
| [`paper-review`](paper-review/) | Writing | Self-review before submission: 5-aspect checklist, adversarial stress-testing, figure/table checks |
| [`paper-rebuttal`](paper-rebuttal/) | Writing | Peer-review rebuttals: score diagnosis, comment prioritization, champion strategy |
| [`academic-slides`](academic-slides/) | Writing | Academic slide decks and conference talks: narrative arc, slide structure, .pptx generation |
| [`study-workflow`](study-workflow/) | Internal | Generates a research workflow diagram (Literature + Writing swim-lanes) as a PNG via gpt-5.4-image-2 |

## Skill Anatomy

```
my-skill/
  SKILL.md          # required — frontmatter + body
  scripts/          # optional — executable scripts invoked via bash tool
  references/       # optional — docs loaded into agent context
```

## SKILL.md Frontmatter

```yaml
---
name: my-skill
description: "One-line summary used for routing."
allowed-tools: Bash          # grants bash tool-calling loop with error feedback
required-keys: [MY_API_KEY]  # optional; skill filtered out at startup if key missing
metadata:
  author: YourName
  version: '1.0.0'
  tags: [relevant, keywords]
---
```

## Adding a Skill

1. Create `skills/my-skill/SKILL.md` with the frontmatter above and your instructions in the body.
2. Add an entry to `skills/skills.json`:
   ```json
   { "id": "my-skill", "description": "One-line description shown to the agent." }
   ```
3. Validate: `python skills/validate_skills.py`

## Validating Skills

```bash
python skills/validate_skills.py
```

Checks all `skills/*/SKILL.md` files for required frontmatter fields, valid `allowed-tools`, and matching `name` vs directory name.
