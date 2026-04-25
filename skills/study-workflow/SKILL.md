---
name: study-workflow
description: Generates a publication-quality research workflow diagram (two swim-lanes: Research and Writing) as a PNG file using matplotlib. Called internally by the pipeline to visualize the study plan.
allowed-tools: Bash
---

This skill generates a crisp, programmatic research workflow diagram using matplotlib.
It is called internally by `_generate_workflow_diagram_async` in `src/nodes.py` and writes
`workflow.png` to the `figures/` subdirectory of the current output directory.
