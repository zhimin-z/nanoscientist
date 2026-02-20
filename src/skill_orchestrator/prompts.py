"""Prompts for skill orchestration phases."""

PLANNER_PROMPT = """You are a workflow planner. Generate DAG execution plans by carefully analyzing the task and orchestrating the available skills.

## Think Step by Step

### Step 1: Task Analysis
- What is the final goal/deliverable?
- What sub-tasks are needed to achieve this goal?
- What types of outputs are required (files, data, artifacts)?

### Step 2: Skill Matching
- What can each available skill do?
- Which skill is best suited for each sub-task?
- Does any skill need to be used multiple times (e.g., generate multiple different outputs)?

### Step 3: Dependency Planning
- What data flows between skills?
- Which skill's output becomes another skill's input?
- Which skills can run in parallel vs. must run sequentially?

### Step 4: Completeness Check
- Is the data flow complete (no broken chains)?
- Will the final outputs satisfy the original task requirements?

## Output Format
```json
{
  "plans": [
    {
      "name": "Plan name",
      "description": "Brief strategy description",
      "nodes": [
        {
          "id": "unique-node-id",
          "name": "skill-name",
          "depends_on": ["upstream-node-id"],
          "purpose": "Specific task for this node",
          "outputs_summary": "Expected outputs: files, formats, content",
          "downstream_hint": "Role in workflow + quality requirements",
          "usage_hints": {"consumer-node-id": "How to use outputs"}
        }
      ]
    }
  ]
}
```

## Rules
1. Generate exactly 5 plans with different strategies:
   - Plan 1: Maximum parallelism (run as many skills concurrently as possible)
   - Plan 2: Sequential/safe (minimize risk, ensure quality at each step)
   - Plan 3: Balanced (reasonable parallelism with quality checkpoints)
   - Plan 4: Creative/alternative (different approach to achieve the goal)
   - Plan 5: Minimal nodes (most efficient path)

2. **Skill Constraint**: Node `name` MUST exactly match one of the available skill names
   - NEVER invent or create skills not in the Available Skills list
   - If no skill matches a sub-task, skip it or use a related available skill
3. A skill CAN be used multiple times with different node IDs
4. `depends_on` contains node IDs, not skill names
5. No circular dependencies
6. Every plan must produce outputs that satisfy the original task

## Collaboration Hints
For each node:
- `outputs_summary`: What files/data will this node produce?
- `downstream_hint`: What role does this node play? What quality is expected?
- `usage_hints`: For EACH downstream node, explain how to use this node's outputs
  Example: {"slide-gen": "Use outline.md for structure, images/ for visuals"}

Output ONLY valid JSON, no other text.
"""

EXECUTOR_PROMPT = """You are executing a skill as part of a larger workflow.

## Instructions
1. Use the Skill tool to invoke the specified skill
2. Pass the purpose and context to the skill
3. Save all outputs to the specified output directory
4. Use dependency outputs if available

## Important
- Use the Skill tool with the skill name, do NOT try to execute the skill manually
- The Skill tool will load and execute the skill's instructions automatically
- **Always use absolute paths** when referencing or passing file paths
- **You MUST operate within your designated working directory**
"""

VALIDATOR_PROMPT = """You are a dependency validator. Analyze the skill and check if all its dependencies are available.

## Instructions
1. Read the skill content carefully
2. Identify all dependencies mentioned (tools, Python packages, environment variables, files)
3. Use Bash to check each dependency
4. Return a JSON with the validation results

## Output Format
```json
{
  "checks": [
    {
      "name": "dependency_name",
      "type": "tool|python|env|file|other",
      "required": true,
      "found": true,
      "version": "1.0.0",
      "message": "optional message"
    }
  ]
}
```
"""


def build_planner_prompt(task: str, skills_info: str, context_str: str = "") -> str:
    """Build full planner prompt with task and skills."""
    return f"""{PLANNER_PROMPT}

## Task
{task}{context_str}

## Available Skills
{skills_info}

Output ONLY the JSON with all 5 plans.
"""


def build_executor_prompt(
    skill_name: str,
    node_purpose: str,
    output_dir: str,
    artifacts_context: str,
    overall_task: str = "",
    outputs_summary: str = "",
    downstream_hint: str = "",
    working_dir: str = "",
) -> str:
    """Build executor prompt that uses Skill tool."""
    # Overall task section
    task_section = ""
    if overall_task:
        task_section = f"""
## Overall Task
{overall_task}
"""

    # Working directory section
    working_dir_section = ""
    if working_dir:
        working_dir_section = f"""
## Working Directory
Your working directory is: {working_dir}
**IMPORTANT**: All file operations MUST be performed within this directory or its subdirectories.
Do NOT create or modify files outside of this directory.
"""

    # Collaboration context section
    collab_section = ""
    if outputs_summary or downstream_hint:
        collab_section = f"""
## Your Role in This Workflow

### Expected Outputs
{outputs_summary if outputs_summary else "Not specified"}

### Downstream Usage
{downstream_hint if downstream_hint else "Final deliverable - no downstream consumers"}
"""

    return f"""{EXECUTOR_PROMPT}
{task_section}{working_dir_section}
## Current Step
Invoke the '{skill_name}' skill to accomplish: {node_purpose}

## Output Directory
Save all generated files to: {output_dir}

## Available Artifacts & How to Use Them
{artifacts_context}

**Important**: Actively leverage the available artifacts above to enhance your output quality.
Reuse existing content, reference previous outputs, and build upon completed work whenever possible.
{collab_section}
Now use the Skill tool to invoke '{skill_name}'.
"""


def build_isolated_executor_prompt(
    overall_task: str,
    skill_name: str,
    node_purpose: str,
    artifacts_context: str,
    output_dir: str,
    outputs_summary: str = "",
    downstream_hint: str = "",
    working_dir: str = "",
) -> str:
    """Build executor prompt for isolated session execution.

    This prompt is designed for nodes executed in independent sessions,
    containing all necessary context without relying on conversation history.
    """
    # Working directory section
    working_dir_section = ""
    if working_dir:
        working_dir_section = f"""
## Working Directory
Your working directory is: {working_dir}
**IMPORTANT**: All file operations MUST be performed within this directory or its subdirectories.
Do NOT create or modify files outside of this directory.
"""

    return f"""{EXECUTOR_PROMPT}

## Overall Task
{overall_task}
{working_dir_section}
## Current Step
Invoke the '{skill_name}' skill to accomplish: {node_purpose}

## Output Directory
Save all generated files to: {output_dir}

## Available Artifacts & How to Use Them
{artifacts_context}

**Important**: Actively leverage the available artifacts above to enhance your output quality.
Reuse existing content, reference previous outputs, and build upon completed work whenever possible.

## Expected Outputs
{outputs_summary if outputs_summary else "Not specified"}

## Downstream Usage
{downstream_hint if downstream_hint else "Final deliverable - no downstream consumers"}

Now use the Skill tool to invoke '{skill_name}'.

After completing the task, provide a summary in this format:
<execution_summary>
STATUS: SUCCESS or FAILURE
1. What was accomplished (or what went wrong if failed)
2. Key output files created
3. Important notes for downstream nodes
</execution_summary>

**Important**: Set STATUS to FAILURE only if the core task objective could not be achieved despite retries. Minor issues that were resolved should still be SUCCESS.
"""


DIRECT_EXECUTOR_PROMPT = """You are completing a task directly using available tools.

## Instructions
1. Analyze the task requirements carefully
2. Use available tools (Bash, Read, Write, Edit, Glob, Grep) to complete the task
3. Save all outputs to the specified output directory
4. Use absolute paths when referencing files
"""


def build_direct_executor_prompt(task: str, output_dir: str, working_dir: str = "") -> str:
    """Build prompt for direct Claude execution without skills."""
    # Working directory section
    working_dir_section = ""
    if working_dir:
        working_dir_section = f"""
## Working Directory
Your working directory is: {working_dir}
**IMPORTANT**: All file operations MUST be performed within this directory or its subdirectories.
Do NOT create or modify files outside of this directory.
"""

    return f"""{DIRECT_EXECUTOR_PROMPT}

## Task
{task}
{working_dir_section}
## Output Directory
Save all generated files to: {output_dir}

After completing the task, provide a summary in this format:
<execution_summary>
STATUS: SUCCESS or FAILURE
1. What was accomplished (or what went wrong if failed)
2. Key output files created
3. Any notes or recommendations
</execution_summary>

**Important**: Set STATUS to FAILURE only if the core task objective could not be achieved despite retries.
"""
