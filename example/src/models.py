"""Domain models for the task management system."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List


class Priority(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class User:
    """Represents a system user who can own and be assigned tasks."""
    id: str
    name: str
    email: str
    role: str = "member"
    created_at: datetime = field(default_factory=datetime.now)

    def is_admin(self) -> bool:
        return self.role == "admin"

    def can_assign_tasks(self) -> bool:
        return self.role in ("admin", "manager")


@dataclass
class Task:
    """Core task entity with lifecycle management."""
    id: str
    title: str
    description: str
    owner_id: str
    status: TaskStatus = TaskStatus.PENDING
    priority: Priority = Priority.MEDIUM
    assignee_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None

    def assign(self, user_id: str) -> None:
        """Assign task to a user. Transitions to IN_PROGRESS if PENDING."""
        self.assignee_id = user_id
        self.updated_at = datetime.now()
        if self.status == TaskStatus.PENDING:
            self.status = TaskStatus.IN_PROGRESS

    def complete(self) -> None:
        """Mark task as completed."""
        if self.status == TaskStatus.CANCELLED:
            raise ValueError("Cannot complete a cancelled task")
        self.status = TaskStatus.COMPLETED
        self.completed_at = datetime.now()
        self.updated_at = datetime.now()

    def cancel(self) -> None:
        """Cancel the task."""
        if self.status == TaskStatus.COMPLETED:
            raise ValueError("Cannot cancel a completed task")
        self.status = TaskStatus.CANCELLED
        self.updated_at = datetime.now()

    @property
    def is_overdue(self) -> bool:
        """Check if task is overdue (placeholder for deadline logic)."""
        return False

    @property
    def is_active(self) -> bool:
        return self.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)


@dataclass
class Comment:
    """Comment on a task for collaboration."""
    id: str
    task_id: str
    author_id: str
    content: str
    created_at: datetime = field(default_factory=datetime.now)

    def edit(self, new_content: str) -> None:
        self.content = new_content
