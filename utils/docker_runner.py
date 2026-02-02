"""Docker and local experiment execution"""
import os
import subprocess
from pathlib import Path
import tempfile
import shutil


def run_experiment(code_dict, mode="local", image="python:3.13", timeout=3600, output_dir=None):
    """
    Run experiment code in Docker or locally

    Args:
        code_dict: Dict with keys:
            - "experiment_py": Python code for experiment.py
            - "requirements_txt": Dependencies for requirements.txt
            - "task_id": Unique identifier for this experiment
        mode: "docker" or "local"
        image: Docker image to use (if mode="docker")
        timeout: Execution timeout in seconds
        output_dir: Parent directory for workspace (defaults to cwd)

    Returns:
        dict: {
            "output": stdout/logs,
            "exit_code": int,
            "workspace": path to workspace directory
        }
    """
    if mode == "docker":
        return _run_docker(code_dict, image, timeout, output_dir=output_dir)
    else:
        return _run_local(code_dict, timeout, output_dir=output_dir)


def _run_docker(code_dict, image, timeout, output_dir=None):
    """Run experiment in Docker container"""
    import docker

    # Create workspace inside output_dir if provided
    parent = Path(output_dir) if output_dir else Path(".")
    workspace = parent / f"workspace_{code_dict.get('task_id', 'default')}"
    workspace.mkdir(parents=True, exist_ok=True)

    # Write files
    (workspace / "experiment.py").write_text(code_dict.get("experiment_py", ""))
    (workspace / "requirements.txt").write_text(code_dict.get("requirements_txt", ""))

    # Run in Docker
    client = docker.from_env()

    try:
        container = client.containers.run(
            image,
            command="bash -c 'pip install -q -r requirements.txt && python experiment.py'",
            volumes={str(workspace.absolute()): {"bind": "/workspace", "mode": "rw"}},
            working_dir="/workspace",
            detach=True,
            mem_limit="4g",
            nano_cpus=2_000_000_000,  # 2 CPUs
            remove=False
        )

        # Wait with timeout
        result = container.wait(timeout=timeout)
        logs = container.logs().decode("utf-8", errors="replace")
        exit_code = result["StatusCode"]

        # Cleanup
        container.remove()

        return {
            "output": logs,
            "exit_code": exit_code,
            "workspace": str(workspace)
        }

    except Exception as e:
        return {
            "output": f"Docker execution failed: {str(e)}",
            "exit_code": -1,
            "workspace": str(workspace)
        }


def _run_local(code_dict, timeout, output_dir=None):
    """Run experiment locally"""
    # Create workspace inside output_dir if provided
    parent = Path(output_dir) if output_dir else Path(".")
    workspace = parent / f"workspace_{code_dict.get('task_id', 'default')}"
    workspace.mkdir(parents=True, exist_ok=True)

    # Write files
    (workspace / "experiment.py").write_text(code_dict.get("experiment_py", ""))
    (workspace / "requirements.txt").write_text(code_dict.get("requirements_txt", ""))

    try:
        # Install dependencies
        subprocess.run(
            ["pip", "install", "-q", "-r", "requirements.txt"],
            cwd=workspace,
            capture_output=True,
            timeout=300  # 5 min for pip install
        )

        # Run experiment
        result = subprocess.run(
            ["python", "experiment.py"],
            cwd=workspace,
            capture_output=True,
            timeout=timeout,
            text=True
        )

        return {
            "output": result.stdout + ("\n" + result.stderr if result.stderr else ""),
            "exit_code": result.returncode,
            "workspace": str(workspace)
        }

    except subprocess.TimeoutExpired:
        return {
            "output": f"Experiment timed out after {timeout} seconds",
            "exit_code": -1,
            "workspace": str(workspace)
        }
    except Exception as e:
        return {
            "output": f"Local execution failed: {str(e)}",
            "exit_code": -1,
            "workspace": str(workspace)
        }


def cleanup_workspace(workspace_path):
    """Remove workspace directory"""
    workspace = Path(workspace_path)
    if workspace.exists():
        shutil.rmtree(workspace)
