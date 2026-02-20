"""Data models for skill orchestration - simplified."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# =============================================================================
# Enums
# =============================================================================


class SkillType(str, Enum):
    """Type of skill in the orchestration graph."""

    PRIMARY = "primary"  # Produces final deliverables
    HELPER = "helper"  # Supports primary or other helpers


class NodeStatus(str, Enum):
    """Execution status of a graph node."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class NodeFailureReason(str, Enum):
    """Reason for node execution failure."""

    SUCCESS = "success"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    SKILL_ERROR = "skill_error"
    DEPENDENCY_FAILED = "dependency_failed"
    UNKNOWN = "unknown"
    EXECUTION_ERROR = "execution_error"


# =============================================================================
# Pydantic Models
# =============================================================================


class SkillMetadata(BaseModel):
    """Metadata parsed from SKILL.md frontmatter."""

    name: str
    description: str
    path: str
    allowed_tools: list[str] = Field(default_factory=list)
    category: str = "other"
    content: str = ""  # SKILL.md content (first 5000 chars)

    class Config:
        frozen = True


class SkillNode(BaseModel):
    """Represents a skill in the dependency graph."""

    id: str
    name: str
    skill_type: SkillType = SkillType.HELPER
    depends_on: list[str] = Field(default_factory=list)
    purpose: str = ""
    status: NodeStatus = NodeStatus.PENDING
    output_path: Optional[str] = None
    # Collaboration fields
    outputs_summary: str = ""  # Expected outputs description
    downstream_hint: str = ""  # Role in workflow + quality requirements
    usage_hints: dict[str, str] = Field(default_factory=dict)  # {consumer_node_id: usage_instruction}

    @property
    def is_terminal(self) -> bool:
        """Check if node is in a terminal state."""
        return self.status in {
            NodeStatus.COMPLETED,
            NodeStatus.FAILED,
            NodeStatus.SKIPPED,
        }

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        result = {
            "id": self.id,
            "name": self.name,
            "type": self.skill_type.value,
            "depends_on": self.depends_on,
            "purpose": self.purpose,
            "status": self.status.value,
        }
        if self.output_path:
            result["output_path"] = self.output_path
        return result


class ExecutionPhase(BaseModel):
    """A group of skills that can run in parallel."""

    phase_number: int
    nodes: list[str]
    mode: str = "parallel"  # parallel | sequential

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "phase": self.phase_number,
            "mode": self.mode,
            "nodes": self.nodes,
        }


class NodeExecutionResult(BaseModel):
    """Result returned from an isolated session execution."""

    node_id: str
    status: NodeStatus
    output_path: Optional[str] = None
    summary: str = ""  # Brief summary of what was accomplished
    error: Optional[str] = None
    failure_reason: NodeFailureReason = NodeFailureReason.SUCCESS
    execution_time_seconds: float = 0.0
    cost_usd: float = 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        result = {
            "node_id": self.node_id,
            "status": self.status.value,
            "failure_reason": self.failure_reason.value,
            "execution_time_seconds": self.execution_time_seconds,
        }
        if self.output_path:
            result["output_path"] = self.output_path
        if self.summary:
            result["summary"] = self.summary
        if self.error:
            result["error"] = self.error
        if self.cost_usd > 0:
            result["cost_usd"] = self.cost_usd
        return result
