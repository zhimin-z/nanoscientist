# PocketFlow Research Pipeline Design

**Date**: 2026-02-02
**Status**: Approved
**Goal**: Redesign mini-researcher-agent using PocketFlow + OpenRouter for fully autonomous end-to-end research

---

## Overview

Replace the current 3-stage waterfall pipeline (smolagents + mini-swe-agent) with a **PocketFlow-based adaptive depth pipeline** that:

1. Classifies research complexity and allocates budget (max rounds)
2. Orchestrates 4 scientific-skills sequentially with iterative refinement
3. Supports Docker-isolated or local experimentation
4. Produces PDF reports (brief doc / technical report / conference draft)

**Key Innovation**: Adaptive budget control where problem complexity determines iteration depth, not model selection.

---

## Architecture

### Core Components

**Framework**: PocketFlow (100-line LLM framework)
**LLM Provider**: OpenRouter (preset model, e.g., claude-3.5-sonnet)
**Budget Model**: Max rounds determined by complexity classification

### Flow Structure

```
┌─────────────────┐
│ User Question   │
└────────┬────────┘
         ▼
┌─────────────────┐
│ Classify        │ → Determine max_rounds (20/120/250)
│ Complexity      │
└────────┬────────┘
         ▼
┌─────────────────┐
│ Budget Guard 1  │ → Check: current_round < max_rounds?
└────┬────────────┘
     ├─ proceed ──────────┐
     └─ emergency_report ─┼──────────┐
                          ▼          │
                  ┌─────────────────┐│
                  │ Literature      ││
                  │ Survey          ││ → Self-loop for refinement
                  │ (iterative)     ││    "continue" → back to self
                  └────┬────────────┘│    "done" → next stage
                       ▼             │
                  ┌─────────────────┐│
                  │ Save Artifacts  ││
                  └────┬────────────┘│
                       ▼             │
                  ┌─────────────────┐│
                  │ Budget Guard 2  ││
                  └────┬────────────┘│
                       ├─ proceed ───┼───────┐
                       └─ emergency ─┘       │
                                             ▼
                                    ┌─────────────────┐
                                    │ Method          │
                                    │ Implementation  │ → Self-loop
                                    │ (iterative)     │
                                    └────┬────────────┘
                                         ▼
                                    ┌─────────────────┐
                                    │ Save Artifacts  │
                                    └────┬────────────┘
                                         ▼
                                    ┌─────────────────┐
                                    │ Budget Guard 3  │
                                    └────┬────────────┘
                                         ├─ proceed ───┐
                                         └─ emergency ─┼──────┐
                                                       ▼      │
                                              ┌─────────────────┐│
                                              │ Experimental    ││
                                              │ Evaluation      ││ → Docker/Local
                                              │ (iterative)     ││
                                              └────┬────────────┘│
                                                   ▼             │
                                              ┌─────────────────┐│
                                              │ Save Artifacts  ││
                                              └────┬────────────┘│
                                                   ▼             │
                                              ┌─────────────────┐│
                                              │ Paper Writing   ││
                                              └────┬────────────┘│
                                                   ▼             ▼
                                              ┌─────────────────┐
                                              │ Final PDF       │
                                              └─────────────────┘
```

### Budget Control

**Complexity Classification** determines max_rounds:
- **Low**: 20 rounds → Brief document (e.g., "Compare sorting algorithms")
- **Medium**: 120 rounds → Technical report (e.g., "Analyze transformer efficiency")
- **High**: 250 rounds → Conference draft (e.g., "Novel attention mechanism with ablations")

**Budget Guards** check before each stage:
- If `current_round < max_rounds` → proceed
- Else → emergency_report (graceful degradation)

**Round Tracking**: Each skill node returns `rounds_used`, accumulated in `shared["current_round"]`

---

## PocketFlow Features Utilized

| Feature | Usage |
|---------|-------|
| **Flow** | Main pipeline orchestration |
| **Node** | Individual skill execution with prep/exec/post lifecycle |
| **Conditional Branching** (`-` syntax) | Budget guards, iteration control |
| **Self-loops** | Iterative refinement (`survey - "continue" >> survey`) |
| **BatchNode** | Parallel database searches (arXiv, DBLP, ACL) |
| **Flow Composition** | Literature survey as sub-flow with parallel search |
| **Built-in Retry** | `Node(max_retries=3, wait=2)` for API robustness |
| **Shared State** | Progressive artifact building across stages |

---

## Data Flow

### Shared State Structure

```python
shared = {
    # Input
    "question": "Research question from user",

    # Budget Control
    "complexity": "low|medium|high",
    "max_rounds": 120,
    "current_round": 0,

    # Stage Outputs
    "survey": {
        "papers": [...],
        "references.bib": "...",
        "survey_report.md": "..."
    },
    "method": {
        "design.md": "...",
        "code/": {...}
    },
    "evaluation": {
        "results.csv": "...",
        "figures/": {...},
        "tables.tex": "..."
    },
    "paper": {
        "main.tex": "...",
        "main.pdf": bytes
    }
}
```

### File System Persistence

```
research_outputs/
└── 20260202_143022_hash/
    ├── state.json              # Full shared state snapshot
    ├── survey/
    │   ├── survey_report.md
    │   └── references.bib
    ├── method/
    │   ├── design.md
    │   └── code/
    ├── evaluation/
    │   ├── results.csv
    │   └── figures/
    └── paper/
        ├── main.tex
        └── main.pdf            # Final deliverable
```

**SaveArtifactsNode** runs after each stage for incremental persistence.

---

## Node Implementations

### 1. ClassifyComplexityNode

**Purpose**: Analyze research question complexity and determine budget

**Logic**:
```python
class ClassifyComplexityNode(Node):
    def exec(self, question):
        prompt = f"""Analyze research complexity:

Question: {question}

Consider:
1. Scope (single algorithm vs system comparison vs novel method)
2. Implementation effort (toy vs full system vs experiments)
3. Evaluation depth (basic metrics vs ablations vs stats)
4. Expected output (brief doc vs technical report vs conference)

Return YAML:
complexity: low|medium|high
reasoning: <why>
max_rounds: <20 for low, 120 for medium, 250 for high>
"""
        return call_llm(prompt)

    def post(self, shared, prep_res, exec_res):
        result = parse_yaml(exec_res)
        shared["complexity"] = result["complexity"]
        shared["max_rounds"] = result["max_rounds"]
        shared["current_round"] = 0
        return "default"
```

### 2. BudgetGuardNode

**Purpose**: Enforce round budget before each stage

**Logic**:
```python
class BudgetGuardNode(Node):
    def exec(self, _):
        pass  # No LLM call

    def post(self, shared, prep_res, exec_res):
        if shared["current_round"] < shared["max_rounds"]:
            return "proceed"
        else:
            shared["budget_exceeded"] = True
            return "emergency_report"
```

### 3. LiteratureSurveyNode

**Purpose**: Execute scientific-skills/literature-survey with iteration

**Features**:
- Loads SKILL.md instructions
- Supports self-loop for refinement
- Tracks rounds_used

**Logic**:
```python
class LiteratureSurveyNode(Node):
    def __init__(self):
        super().__init__()
        self.skill = load_skill("scientific-skills/literature-survey/SKILL.md")

    def exec(self, inputs):
        prompt = f"""Execute literature survey:

{inputs['skill']}

Question: {inputs['question']}
Budget: {inputs['rounds_left']} rounds

Return YAML:
status: continue|done
output: <markdown survey>
rounds_used: <int>
"""
        return call_llm(prompt)

    def post(self, shared, prep_res, exec_res):
        result = parse_yaml(exec_res)
        shared["survey_output"] = result["output"]
        shared["current_round"] += result["rounds_used"]
        return result["status"]  # "continue" or "done"
```

**Flow Construction**:
```python
survey = LiteratureSurveyNode()
method = MethodImplementationNode()

# Self-loop for iteration
survey - "continue" >> survey
# Move to next stage when done
survey - "done" >> method
```

### 4. ParallelDatabaseSearchBatchNode (Sub-component)

**Purpose**: Parallel literature search across databases (arXiv, DBLP, ACL)

**Uses PocketFlow's BatchNode**:
```python
class ParallelDatabaseSearchBatchNode(BatchNode):
    def prep(self, shared):
        # Return list of search tasks (PocketFlow parallelizes)
        return [
            {"db": "arxiv", "query": shared["question"]},
            {"db": "dblp", "query": shared["question"]},
            {"db": "acl", "query": shared["question"]}
        ]

    def exec(self, search_task):
        # Called once per task in parallel
        return search_database(search_task["db"], search_task["query"])

    def post(self, shared, prep_res, exec_res):
        # Merge all parallel results
        shared["search_results"] = merge_results(exec_res)
        return "default"
```

### 5. ExperimentalEvaluationNode

**Purpose**: Execute experiments with Docker or local mode

**Configuration-driven**:
```python
class ExperimentalEvaluationNode(Node):
    def __init__(self):
        super().__init__()
        self.enable_docker = os.getenv("ENABLE_DOCKER", "False").lower() == "true"

    def exec(self, inputs):
        # Generate experiment code using skill
        code = generate_experiment_code(inputs)

        # Run based on mode
        if self.enable_docker:
            return run_docker_experiment(code, image="python:3.13")
        else:
            return run_local_experiment(code)
```

**Docker Execution**:
```python
def run_docker_experiment(code, image="python:3.13"):
    import docker
    client = docker.from_env()
    workspace = setup_workspace(code)

    container = client.containers.run(
        image,
        command="bash -c 'pip install -r requirements.txt && python experiment.py'",
        volumes={str(workspace): '/workspace'},
        working_dir='/workspace',
        detach=True,
        mem_limit='4g'
    )

    result = container.wait(timeout=3600)
    logs = container.logs().decode()
    container.remove()
    return {"output": logs, "exit_code": result["StatusCode"]}
```

---

## Configuration

### Environment Variables (.env)

```bash
# LLM Provider
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=anthropic/claude-3.5-sonnet

# Experimentation Mode
ENABLE_DOCKER=False          # True for isolated, False for fast local
DOCKER_IMAGE=python:3.13
DOCKER_TIMEOUT=3600

# Budget (optional overrides)
MAX_ROUNDS_LOW=20
MAX_ROUNDS_MEDIUM=120
MAX_ROUNDS_HIGH=250
```

---

## Dependencies

### requirements.txt (Refactored)

**Removed**:
- ❌ `smolagents` - Replaced by PocketFlow
- ❌ `mini-swe-agent` - LLM generates code directly
- ❌ `litellm` - Using OpenAI SDK (OpenRouter compatible)
- ❌ `tavily-python` - Optional, can use LLM tools

**Added**:
- ✅ `pocketflow>=0.0.3` - Core framework

**Kept**:
- `openai>=1.0.0` - OpenRouter uses OpenAI SDK format
- `docker>=7.0.0` - For isolated experiments (optional)
- Scientific skills dependencies (arxiv, numpy, pandas, matplotlib, etc.)

**Size**: ~170MB → ~50MB reduction

---

## File Structure

```
mini-researcher-agent/
├── main.py                          # ~250 lines - PocketFlow orchestration
├── requirements.txt                 # Minimal dependencies
├── .env                            # Configuration
│
├── utils/
│   ├── llm.py                      # OpenRouter integration
│   ├── skill_loader.py             # Parse SKILL.md files
│   └── docker_runner.py            # Docker execution helpers
│
├── scientific-skills/              # Existing skills (unchanged)
│   ├── literature-survey/
│   ├── method-implementation/
│   ├── experimental-evaluation/
│   └── paper-writing/
│
├── research_outputs/               # Generated artifacts
│   └── 20260202_143022_hash/
│       ├── state.json
│       ├── survey/
│       ├── method/
│       ├── evaluation/
│       └── paper/
│
└── docs/
    └── plans/
        └── 2026-02-02-pocketflow-research-pipeline-design.md (this file)
```

---

## Error Handling

### 1. Budget Enforcement

**BudgetGuardNode** checks before each stage:
```python
if shared["current_round"] < shared["max_rounds"]:
    return "proceed"
else:
    return "emergency_report"  # Graceful degradation
```

### 2. API Retry (PocketFlow Built-in)

```python
class RobustAPINode(Node):
    def __init__(self):
        super().__init__(max_retries=3, wait=2)  # Built-in

    def exec_fallback(self, prep_res, exc):
        # Called after max_retries exhausted
        return {"error": str(exc), "output": "API unavailable"}
```

### 3. Graceful Degradation

```python
synthesis - "success" >> quality_check
synthesis - "partial" >> emergency_output
synthesis - "failed" >> human_notification
```

---

## Usage Examples

### Basic Usage

```bash
# Local mode (fast)
ENABLE_DOCKER=False python main.py "Compare quicksort vs mergesort performance"

# Docker mode (isolated)
ENABLE_DOCKER=True python main.py "Novel attention mechanism for transformers"
```

### Expected Outputs

**Low Complexity (20 rounds)**:
```
Question: "Compare quicksort vs mergesort"
Complexity: low
Max rounds: 20
Output: research_outputs/.../paper/main.pdf (8 pages, brief comparison)
```

**Medium Complexity (120 rounds)**:
```
Question: "Analyze transformer efficiency techniques"
Complexity: medium
Max rounds: 120
Output: research_outputs/.../paper/main.pdf (15 pages, technical report)
```

**High Complexity (250 rounds)**:
```
Question: "Novel sparse attention with ablation studies"
Complexity: high
Max rounds: 250
Output: research_outputs/.../paper/main.pdf (25 pages, conference draft)
```

---

## Implementation Plan

### Phase 1: Core Infrastructure
1. ✅ Design validation (this document)
2. Refactor requirements.txt
3. Implement utils/ modules:
   - llm.py (OpenRouter client)
   - skill_loader.py (SKILL.md parser)
   - docker_runner.py (Docker/local execution)

### Phase 2: Node Implementation
1. ClassifyComplexityNode
2. BudgetGuardNode
3. LiteratureSurveyNode
4. MethodImplementationNode
5. ExperimentalEvaluationNode
6. PaperWritingNode
7. SaveArtifactsNode

### Phase 3: Flow Construction
1. Build main pipeline with guards
2. Add self-loops for iteration
3. Wire emergency report paths

### Phase 4: Testing
1. Test with low complexity question
2. Test with medium complexity question
3. Test with high complexity question
4. Verify budget enforcement
5. Test Docker vs local modes

---

## Success Criteria

- [x] Design approved by user
- [ ] main.py under 300 lines
- [ ] requirements.txt under 20 dependencies
- [ ] Successfully generates PDF for all complexity levels
- [ ] Budget enforcement works (stops at max_rounds)
- [ ] Docker and local modes both functional
- [ ] All 4 scientific-skills successfully orchestrated

---

## Future Enhancements

1. **Web UI**: Add FastAPI + WebSocket for real-time progress
2. **Human-in-the-loop**: Add approval nodes between stages
3. **Cost tracking**: Log OpenRouter API costs per stage
4. **Parallel evaluation**: Use BatchNode for multiple baselines
5. **Incremental checkpointing**: Resume from last successful stage

---

**End of Design Document**
