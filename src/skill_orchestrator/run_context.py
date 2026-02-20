"""Run context management for isolated execution."""

import hashlib
import shutil
import json
from datetime import datetime
from pathlib import Path
from typing import Optional


class RunContext:
    """Manage isolated environment for single execution.

    Each execution creates an independent run directory containing:
    - .claude/skills/  Copy of required skills
    - workspace/       Node output directory
    - logs/            Execution logs
    - meta.json        Execution metadata
    - result.json      Execution result
    """

    def __init__(self, run_id: str, base_dir: Path):
        self.run_id = run_id
        self.run_dir = base_dir / run_id
        self.skills_dir = self.run_dir / ".claude" / "skills"
        self.workspace_dir = self.run_dir / "workspace"
        self.logs_dir = self.run_dir / "logs"
        self._setup_done = False

    @classmethod
    def create(cls, task: str, base_dir: str = "runs", mode: str = None, task_name: str = None) -> "RunContext":
        """Create a new execution context.

        Args:
            task: Task description for generating hash
            base_dir: Path to runs directory
            mode: Execution mode (dag, auto_selected, auto_all, baseline)
            task_name: User-specified task name (optional)

        Returns:
            RunContext instance
        """
        import re
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        task_hash = hashlib.md5(task.encode()).hexdigest()[:6]

        parts = [timestamp]
        if mode:
            parts.append(mode)
        if task_name:
            safe_name = cls._sanitize_name(task_name)
            if safe_name:
                parts.append(safe_name)
        parts.append(task_hash)

        run_id = "-".join(parts)
        return cls(run_id, Path(base_dir))

    @staticmethod
    def _sanitize_name(name: str, max_length: int = 30) -> str:
        """Sanitize task name for safe folder naming.

        Args:
            name: Raw task name
            max_length: Maximum length of sanitized name

        Returns:
            Sanitized name safe for use in folder names
        """
        import re
        sanitized = name.replace(" ", "_")
        sanitized = re.sub(r'[^a-zA-Z0-9_-]', '', sanitized)
        sanitized = sanitized.lower()[:max_length].rstrip('_-')
        return sanitized

    def setup(
        self,
        skill_names: list[str],
        source_skill_dir: Path,
        copy_all: bool = False,
    ) -> None:
        """Initialize run directory structure and copy skills.

        Args:
            skill_names: List of skill names to copy
            source_skill_dir: Source skill directory (usually .claude/skills)
            copy_all: If True, copy all skills; otherwise only copy skill_names
        """
        if self._setup_done:
            return

        # Create base directory structure
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        if copy_all:
            # Copy all skills
            if source_skill_dir.exists():
                self.skills_dir.mkdir(parents=True, exist_ok=True)
                for skill_dir in source_skill_dir.iterdir():
                    if skill_dir.is_dir():
                        dst = self.skills_dir / skill_dir.name
                        shutil.copytree(skill_dir, dst, dirs_exist_ok=True)
        elif skill_names:
            # Copy only specified skills
            self.skills_dir.mkdir(parents=True, exist_ok=True)
            for name in skill_names:
                src = source_skill_dir / name
                dst = self.skills_dir / name
                if src.exists():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
        # If skill_names is empty and copy_all is False, don't create .claude/skills/

        self._setup_done = True

    def copy_files(self, file_paths: list[str]) -> list[str]:
        """Copy specified files to workspace directory.

        Args:
            file_paths: List of file paths (supports absolute and relative paths)

        Returns:
            List of successfully copied filenames
        """
        copied = []
        for path_str in file_paths:
            src = Path(path_str).expanduser().resolve()
            if not src.exists():
                continue
            dst = self.workspace_dir / src.name
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
            copied.append(src.name)
        return copied

    def save_meta(self, task: str, mode: str, skills: list[str]) -> None:
        """Save execution metadata.

        Args:
            task: Task description
            mode: Execution mode
            skills: List of used skills
        """
        meta = {
            "run_id": self.run_id,
            "task": task,
            "mode": mode,
            "skills": skills,
            "started_at": datetime.now().isoformat(),
        }
        with open(self.run_dir / "meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

    def update_meta(self, **kwargs) -> None:
        """Update metadata fields.

        Args:
            **kwargs: Fields to update
        """
        meta_path = self.run_dir / "meta.json"
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        else:
            meta = {}

        meta.update(kwargs)
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

    def save_result(self, result: dict) -> None:
        """Save execution result.

        Args:
            result: Execution result dictionary
        """
        # Also update completed_at in meta.json
        self.update_meta(completed_at=datetime.now().isoformat())

        with open(self.run_dir / "result.json", "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

    def save_plan(self, plan: dict) -> None:
        """Save execution plan.

        Args:
            plan: Execution plan dictionary
        """
        with open(self.run_dir / "plan.json", "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)


class RunManager:
    """Manage runs directory."""

    def __init__(self, base_dir: str = "runs"):
        self.base_dir = Path(base_dir)

    def cleanup_old_runs(self, keep_count: int = 10) -> int:
        """Keep recent N executions and delete old ones.

        Args:
            keep_count: Number of executions to keep

        Returns:
            Number of deleted directories
        """
        if not self.base_dir.exists():
            return 0

        runs = sorted(
            [d for d in self.base_dir.iterdir() if d.is_dir()],
            reverse=True
        )

        deleted = 0
        for run_dir in runs[keep_count:]:
            shutil.rmtree(run_dir)
            deleted += 1

        return deleted

    def list_runs(self) -> list[dict]:
        """List all execution records.

        Returns:
            List of execution records in reverse chronological order
        """
        runs = []
        if not self.base_dir.exists():
            return runs

        for run_dir in sorted(self.base_dir.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            meta_file = run_dir / "meta.json"
            if meta_file.exists():
                with open(meta_file, encoding="utf-8") as f:
                    meta = json.load(f)
                    meta["run_dir"] = str(run_dir)
                    runs.append(meta)

        return runs

    def get_run(self, run_id: str) -> Optional[dict]:
        """Get specified execution record.

        Args:
            run_id: Execution ID

        Returns:
            Execution record, or None if not found
        """
        run_dir = self.base_dir / run_id
        if not run_dir.exists():
            return None

        meta_file = run_dir / "meta.json"
        if not meta_file.exists():
            return None

        with open(meta_file, encoding="utf-8") as f:
            meta = json.load(f)

        result_file = run_dir / "result.json"
        if result_file.exists():
            with open(result_file, encoding="utf-8") as f:
                meta["result"] = json.load(f)

        return meta
