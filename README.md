# MSR-Scientist 🔬

**A minimalist self-evolving researcher agent using [smolagents](https://github.com/huggingface/smolagents).**

One file. Three tools. Infinite research potential.

```
PLAN → IMPLEMENT → VERIFY → SHIP
```

## Features

✅ **Hypothesis generation** - Formulate research questions
✅ **Autonomous experimentation** - Execute code, install packages
✅ **Paper drafting** - Generate LaTeX papers
✅ **Self-evolution** - Creates tools and adapts as needed

## Installation

```bash
pip install -r requirements.txt
export HF_TOKEN=your_huggingface_token
```

## Quick Start

```python
from msr_scientist import create_agent

agent = create_agent()
agent.research("Compare bubble sort vs quicksort performance")
```

That's it! The agent will:
1. Generate hypotheses
2. Install any needed packages
3. Run experiments
4. Draft a research paper

## CLI Usage

```bash
python msr_scientist.py "Your research task here"
```

## How It Works

The agent has 3 core tools:

- `run_experiment(code)` - Execute Python/bash code
- `install_package(name)` - Install packages autonomously
- `draft_paper(...)` - Generate LaTeX papers

**Self-evolution** is guided by a system prompt that teaches the agent to:
- Install packages when needed
- Write code to create new capabilities
- Follow the research cycle: plan → implement → verify → ship

## Example

```python
from msr_scientist import create_agent

agent = create_agent()

agent.research("""
Investigate: Are Python list comprehensions faster than loops?

1. Formulate hypothesis
2. Design timing experiments
3. Run tests with different data sizes
4. Analyze results
5. Draft paper with findings
""")
```

Check `outputs/` for generated papers and results.

## Architecture

```
MSR-Scientist/
├── msr_scientist.py   # Everything (agent + tools + prompt)
├── example.py         # Usage example
├── requirements.txt   # Dependencies
└── README.md         # This file
```

**Total: ~250 lines of code** 🎯

## Philosophy

- **Minimal**: One file, essential tools only
- **Autonomous**: Agent installs packages and evolves itself
- **Research-focused**: Built for hypothesis → experiment → paper workflows
- **Self-evolving**: Guided by system prompt, not rigid code

## Requirements

- Python 3.9+
- HuggingFace API token
- LaTeX (optional, for PDF compilation)

## License

Apache-2.0

## Inspired By

- [mini-swe-agent](https://github.com/OpenDevin/mini-swe-agent)
- [smolagents](https://github.com/huggingface/smolagents)

---

**Start researching:**

```python
from msr_scientist import create_agent
create_agent().research("Your question here")
```

🔬 **Happy researching!**
