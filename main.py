#!/usr/bin/env python3
"""
Autonomous Research Agent using PocketFlow + OpenRouter

Executes scientific-skills pipeline with adaptive budget control
Input: Research question
Output: PDF paper (brief/technical/conference based on complexity)
"""
import os
import sys
import json
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

from pocketflow import Flow, Node
from utils.llm import call_llm, parse_yaml
from utils.skill_loader import get_skill_instructions
from utils.docker_runner import run_experiment

# Load environment variables
load_dotenv()

# Configuration
ENABLE_DOCKER = os.getenv("ENABLE_DOCKER", "False").lower() == "true"
DOCKER_IMAGE = os.getenv("DOCKER_IMAGE", "python:3.13")


# ============================================================================
# Node Definitions
# ============================================================================

class ClassifyComplexityNode(Node):
    """Analyze research question and determine budget (max_rounds)"""

    def prep(self, shared):
        return shared["question"]

    def exec(self, question):
        prompt = f"""Analyze this research question and classify its complexity:

Question: {question}

Consider:
1. Scope: Single algorithm vs system comparison vs novel method
2. Implementation effort: Toy example vs full system vs experiments
3. Evaluation depth: Basic metrics vs ablations vs statistical tests
4. Expected output: Brief document vs technical report vs conference paper

Return YAML:
```yaml
complexity: low|medium|high
reasoning: <detailed explanation of why this classification>
max_rounds: <20 for low, 120 for medium, 250 for high>
expected_output: brief|technical|conference
```

Be conservative - if uncertain, choose lower complexity.
"""
        return call_llm(prompt)

    def post(self, shared, prep_res, exec_res):
        try:
            result = parse_yaml(exec_res)
            shared["complexity"] = result["complexity"]
            shared["max_rounds"] = result["max_rounds"]
            shared["expected_output"] = result.get("expected_output", "technical")
            shared["current_round"] = 0
            shared["complexity_reasoning"] = result.get("reasoning", "")

            print(f"\n{'='*80}")
            print(f"Complexity Classification:")
            print(f"  Level: {result['complexity'].upper()}")
            print(f"  Max Rounds: {result['max_rounds']}")
            print(f"  Expected Output: {result.get('expected_output', 'technical')}")
            print(f"  Reasoning: {result.get('reasoning', '')[:100]}...")
            print(f"{'='*80}\n")
        except Exception as e:
            print(f"⚠️  Classification failed: {e}. Defaulting to medium complexity.")
            shared["complexity"] = "medium"
            shared["max_rounds"] = 120
            shared["expected_output"] = "technical"
            shared["current_round"] = 0

        return "default"


class BudgetGuardNode(Node):
    """Check if budget allows proceeding to next stage"""

    def __init__(self, stage_name):
        super().__init__()
        self.stage_name = stage_name

    def exec(self, _):
        pass  # No LLM call needed

    def post(self, shared, prep_res, exec_res):
        current = shared["current_round"]
        max_rounds = shared["max_rounds"]

        if current < max_rounds:
            print(f"✓ Budget check passed: {current}/{max_rounds} rounds used")
            return "proceed"
        else:
            print(f"⚠️  Budget exceeded at {self.stage_name}: {current}/{max_rounds} rounds")
            shared["budget_exceeded"] = True
            shared["termination_stage"] = self.stage_name
            return "emergency_report"


class LiteratureSurveyNode(Node):
    """Execute literature-survey skill with iteration support"""

    def __init__(self):
        super().__init__()
        self.skill = get_skill_instructions("scientific-skills/literature-survey/SKILL.md")

    def prep(self, shared):
        return {
            "skill": self.skill,
            "question": shared["question"],
            "previous_output": shared.get("survey_output"),
            "rounds_left": shared["max_rounds"] - shared["current_round"],
            "iteration": shared.get("survey_iteration", 0)
        }

    def exec(self, inputs):
        prompt = f"""Execute the literature survey skill:

{inputs['skill']}

Research Question: {inputs['question']}
Budget Remaining: {inputs['rounds_left']} rounds
Current Iteration: {inputs['iteration']}

{"Previous Output (refine if needed):" if inputs['previous_output'] else "First iteration - start from scratch."}
{inputs['previous_output'] or ""}

Return YAML with:
```yaml
status: continue|done  # continue if needs refinement, done if satisfied
output:
  survey_report_md: |
    <comprehensive markdown literature survey>
  references_bib: |
    <BibTeX bibliography>
  key_papers: <list of most important papers>
rounds_used: <integer: how many rounds this iteration consumed>
reasoning: <why continue or done>
```

If status is "done", ensure the survey is comprehensive and publication-ready.
"""
        return call_llm(prompt, max_tokens=8192)

    def post(self, shared, prep_res, exec_res):
        try:
            result = parse_yaml(exec_res)

            # Update outputs
            shared["survey_output"] = result.get("output", {})
            shared["current_round"] += result.get("rounds_used", 1)
            shared["survey_iteration"] = shared.get("survey_iteration", 0) + 1

            status = result.get("status", "done")
            print(f"\n📚 Literature Survey - Iteration {shared['survey_iteration']}")
            print(f"   Status: {status}")
            print(f"   Rounds used: {result.get('rounds_used', 1)}")
            print(f"   Total rounds: {shared['current_round']}/{shared['max_rounds']}")

            return status
        except Exception as e:
            print(f"⚠️  Literature survey failed: {e}")
            shared["survey_output"] = {"error": str(e)}
            shared["current_round"] += 5  # Penalty for failure
            return "done"  # Force proceed even on error


class MethodImplementationNode(Node):
    """Execute method-implementation skill with iteration support"""

    def __init__(self):
        super().__init__()
        self.skill = get_skill_instructions("scientific-skills/method-implementation/SKILL.md")

    def prep(self, shared):
        return {
            "skill": self.skill,
            "question": shared["question"],
            "survey": shared.get("survey_output", {}),
            "previous_output": shared.get("method_output"),
            "rounds_left": shared["max_rounds"] - shared["current_round"],
            "iteration": shared.get("method_iteration", 0)
        }

    def exec(self, inputs):
        prompt = f"""Execute the method implementation skill:

{inputs['skill']}

Research Question: {inputs['question']}
Literature Survey Results: {json.dumps(inputs['survey'], indent=2)}
Budget Remaining: {inputs['rounds_left']} rounds
Current Iteration: {inputs['iteration']}

{"Previous Output:" if inputs['previous_output'] else "First iteration."}
{json.dumps(inputs['previous_output'], indent=2) if inputs['previous_output'] else ""}

Return YAML with:
```yaml
status: continue|done
output:
  design_md: |
    <method design document>
  code_files:
    - filename: <name>
      content: |
        <code>
  requirements_txt: |
    <dependencies>
rounds_used: <integer>
reasoning: <why continue or done>
```
"""
        return call_llm(prompt, max_tokens=8192)

    def post(self, shared, prep_res, exec_res):
        try:
            result = parse_yaml(exec_res)

            shared["method_output"] = result.get("output", {})
            shared["current_round"] += result.get("rounds_used", 1)
            shared["method_iteration"] = shared.get("method_iteration", 0) + 1

            status = result.get("status", "done")
            print(f"\n🔧 Method Implementation - Iteration {shared['method_iteration']}")
            print(f"   Status: {status}")
            print(f"   Rounds used: {result.get('rounds_used', 1)}")
            print(f"   Total rounds: {shared['current_round']}/{shared['max_rounds']}")

            return status
        except Exception as e:
            print(f"⚠️  Method implementation failed: {e}")
            shared["method_output"] = {"error": str(e)}
            shared["current_round"] += 5
            return "done"


class ExperimentalEvaluationNode(Node):
    """Execute experiments using Docker or locally"""

    def __init__(self):
        super().__init__()
        self.skill = get_skill_instructions("scientific-skills/experimental-evaluation/SKILL.md")

    def prep(self, shared):
        return {
            "skill": self.skill,
            "question": shared["question"],
            "method": shared.get("method_output", {}),
            "previous_results": shared.get("evaluation_output"),
            "rounds_left": shared["max_rounds"] - shared["current_round"],
            "iteration": shared.get("evaluation_iteration", 0)
        }

    def exec(self, inputs):
        # Generate experiment code
        prompt = f"""Execute experimental evaluation:

{inputs['skill']}

Research Question: {inputs['question']}
Method Implementation: {json.dumps(inputs['method'], indent=2)}
Budget Remaining: {inputs['rounds_left']} rounds
Current Iteration: {inputs['iteration']}

Generate experiment code that will:
1. Run the implemented method
2. Collect metrics
3. Generate plots and tables

Return YAML with:
```yaml
status: continue|done
experiment_code:
  experiment_py: |
    <Python code for experiment.py>
  requirements_txt: |
    <dependencies>
expected_outputs:
  - results.csv
  - figures/plot1.png
  - tables.tex
rounds_used: <integer>
reasoning: <why continue or done>
```

The experiment code should be self-contained and save results to files.
"""
        response = call_llm(prompt, max_tokens=8192)
        result = parse_yaml(response)

        # Run experiment if code generated
        if "experiment_code" in result:
            print(f"\n🧪 Running experiment ({'Docker' if ENABLE_DOCKER else 'Local'})...")

            task_id = f"eval_{inputs['iteration']}"
            exec_result = run_experiment(
                {
                    "experiment_py": result["experiment_code"].get("experiment_py", ""),
                    "requirements_txt": result["experiment_code"].get("requirements_txt", ""),
                    "task_id": task_id
                },
                mode="docker" if ENABLE_DOCKER else "local",
                image=DOCKER_IMAGE
            )

            result["experiment_output"] = exec_result
            print(f"   Exit code: {exec_result['exit_code']}")
            print(f"   Workspace: {exec_result['workspace']}")

        return json.dumps(result)

    def post(self, shared, prep_res, exec_res):
        try:
            result = json.loads(exec_res)

            shared["evaluation_output"] = result
            shared["current_round"] += result.get("rounds_used", 1)
            shared["evaluation_iteration"] = shared.get("evaluation_iteration", 0) + 1

            status = result.get("status", "done")
            print(f"\n📊 Experimental Evaluation - Iteration {shared['evaluation_iteration']}")
            print(f"   Status: {status}")
            print(f"   Rounds used: {result.get('rounds_used', 1)}")
            print(f"   Total rounds: {shared['current_round']}/{shared['max_rounds']}")

            return status
        except Exception as e:
            print(f"⚠️  Experimental evaluation failed: {e}")
            shared["evaluation_output"] = {"error": str(e)}
            shared["current_round"] += 5
            return "done"


class PaperWritingNode(Node):
    """Generate final PDF paper"""

    def __init__(self, emergency=False):
        super().__init__()
        self.skill = get_skill_instructions("scientific-skills/paper-writing/SKILL.md")
        self.emergency = emergency

    def prep(self, shared):
        return {
            "skill": self.skill,
            "question": shared["question"],
            "survey": shared.get("survey_output", {}),
            "method": shared.get("method_output", {}),
            "evaluation": shared.get("evaluation_output", {}),
            "expected_output": shared.get("expected_output", "technical"),
            "budget_exceeded": shared.get("budget_exceeded", False),
            "emergency": self.emergency
        }

    def exec(self, inputs):
        mode = "EMERGENCY - Brief summary of completed work" if inputs["emergency"] else "Full paper"

        prompt = f"""Generate academic paper ({mode}):

{inputs['skill']}

Research Question: {inputs['question']}
Expected Output Type: {inputs['expected_output']}

Available Content:
- Literature Survey: {json.dumps(inputs['survey'], indent=2)[:500]}...
- Method: {json.dumps(inputs['method'], indent=2)[:500]}...
- Evaluation: {json.dumps(inputs['evaluation'], indent=2)[:500]}...

{"⚠️ EMERGENCY MODE: Budget exceeded. Generate brief summary paper with available results." if inputs['emergency'] else ""}

Return YAML with:
```yaml
paper:
  main_tex: |
    <complete LaTeX paper using ACM template>
  references_bib: |
    <BibTeX bibliography>
  title: <paper title>
  abstract: <paper abstract>
```

Include all sections: Abstract, Introduction, Related Work, Method, Experiments, Conclusion.
"""
        return call_llm(prompt, max_tokens=16384)

    def post(self, shared, prep_res, exec_res):
        try:
            result = parse_yaml(exec_res)
            shared["paper_output"] = result.get("paper", {})

            print(f"\n📄 Paper Writing Complete")
            print(f"   Title: {result.get('paper', {}).get('title', 'Untitled')}")
            print(f"   Mode: {'Emergency (partial results)' if self.emergency else 'Full paper'}")

            return "default"
        except Exception as e:
            print(f"⚠️  Paper writing failed: {e}")
            shared["paper_output"] = {"error": str(e)}
            return "default"


class SaveArtifactsNode(Node):
    """Save all artifacts to disk"""

    def __init__(self, stage_name):
        super().__init__()
        self.stage_name = stage_name

    def exec(self, inputs):
        pass  # No LLM call

    def post(self, shared, prep_res, exec_res):
        # Create output directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        question_hash = abs(hash(shared["question"])) % 10000
        output_dir = Path(f"research_outputs/{timestamp}_{question_hash}")
        output_dir.mkdir(parents=True, exist_ok=True)

        # Save full state
        state_file = output_dir / "state.json"
        with open(state_file, "w") as f:
            # Convert Path objects to strings for JSON serialization
            json_safe_shared = {k: str(v) if isinstance(v, Path) else v
                              for k, v in shared.items()}
            json.dump(json_safe_shared, f, indent=2, default=str)

        # Save stage-specific outputs
        if "survey_output" in shared and self.stage_name == "survey":
            survey_dir = output_dir / "survey"
            survey_dir.mkdir(exist_ok=True)
            survey = shared["survey_output"]
            if isinstance(survey, dict):
                if "survey_report_md" in survey:
                    (survey_dir / "survey_report.md").write_text(survey["survey_report_md"])
                if "references_bib" in survey:
                    (survey_dir / "references.bib").write_text(survey["references_bib"])

        if "method_output" in shared and self.stage_name == "method":
            method_dir = output_dir / "method"
            method_dir.mkdir(exist_ok=True)
            method = shared["method_output"]
            if isinstance(method, dict):
                if "design_md" in method:
                    (method_dir / "design.md").write_text(method["design_md"])
                if "code_files" in method:
                    code_dir = method_dir / "code"
                    code_dir.mkdir(exist_ok=True)
                    for code_file in method["code_files"]:
                        (code_dir / code_file["filename"]).write_text(code_file["content"])

        if "paper_output" in shared and self.stage_name == "paper":
            paper_dir = output_dir / "paper"
            paper_dir.mkdir(exist_ok=True)
            paper = shared["paper_output"]
            if isinstance(paper, dict):
                if "main_tex" in paper:
                    (paper_dir / "main.tex").write_text(paper["main_tex"])
                if "references_bib" in paper:
                    (paper_dir / "references.bib").write_text(paper["references_bib"])

        shared["output_dir"] = str(output_dir)
        print(f"💾 Saved {self.stage_name} artifacts to: {output_dir}")

        return "default"


# ============================================================================
# Flow Construction
# ============================================================================

def create_research_pipeline():
    """Build the complete research pipeline flow"""

    # Initialize all nodes
    classify = ClassifyComplexityNode()

    guard1 = BudgetGuardNode("literature_survey")
    survey = LiteratureSurveyNode()
    save1 = SaveArtifactsNode("survey")

    guard2 = BudgetGuardNode("method_implementation")
    method = MethodImplementationNode()
    save2 = SaveArtifactsNode("method")

    guard3 = BudgetGuardNode("experimental_evaluation")
    evaluation = ExperimentalEvaluationNode()
    save3 = SaveArtifactsNode("evaluation")

    paper = PaperWritingNode(emergency=False)
    emergency_paper = PaperWritingNode(emergency=True)
    save_final = SaveArtifactsNode("paper")

    # Build flow with budget guards and self-loops
    pipeline = Flow(start=classify)

    # Stage 1: Literature Survey
    classify >> guard1
    guard1 - "proceed" >> survey
    guard1 - "emergency_report" >> emergency_paper

    survey - "continue" >> survey  # Self-loop for refinement
    survey - "done" >> save1 >> guard2

    # Stage 2: Method Implementation
    guard2 - "proceed" >> method
    guard2 - "emergency_report" >> emergency_paper

    method - "continue" >> method  # Self-loop
    method - "done" >> save2 >> guard3

    # Stage 3: Experimental Evaluation
    guard3 - "proceed" >> evaluation
    guard3 - "emergency_report" >> emergency_paper

    evaluation - "continue" >> evaluation  # Self-loop
    evaluation - "done" >> save3 >> paper

    # Final output
    paper >> save_final
    emergency_paper >> save_final

    return pipeline


# ============================================================================
# Main Entry Point
# ============================================================================

def main():
    """Main entry point for research agent"""

    if len(sys.argv) < 2:
        print("Usage: python main.py '<research question>'")
        print("\nExample:")
        print("  python main.py 'Compare quicksort vs mergesort performance'")
        sys.exit(1)

    question = " ".join(sys.argv[1:])

    print(f"\n{'='*80}")
    print("Mini Researcher Agent - PocketFlow Edition")
    print(f"{'='*80}")
    print(f"\nResearch Question: {question}")
    print(f"Docker Mode: {'Enabled' if ENABLE_DOCKER else 'Disabled (local)'}")
    print(f"Model: {os.getenv('OPENROUTER_MODEL', 'anthropic/claude-3.5-sonnet')}")

    # Initialize shared state
    shared = {
        "question": question,
        "start_time": datetime.now().isoformat()
    }

    # Create and run pipeline
    try:
        pipeline = create_research_pipeline()
        result = pipeline.run(shared)

        # Print final summary
        print(f"\n{'='*80}")
        print("Research Complete!")
        print(f"{'='*80}")
        print(f"Complexity: {shared.get('complexity', 'unknown').upper()}")
        print(f"Rounds Used: {shared.get('current_round', 0)}/{shared.get('max_rounds', 0)}")
        print(f"Output Directory: {shared.get('output_dir', 'N/A')}")

        if shared.get("budget_exceeded"):
            print(f"\n⚠️  Budget exceeded at stage: {shared.get('termination_stage', 'unknown')}")
            print("   Emergency paper generated with partial results.")

        print(f"\n{'='*80}\n")

    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Pipeline failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
