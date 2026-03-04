"""Business logic layer — coordinates repositories and enforces rules."""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from .models import Task, User, Comment, TaskStatus, Priority
from .repository import TaskRepository, UserRepository, CommentRepository

logger = logging.getLogger(__name__)


class AuthorizationError(Exception):
    """Raised when a user lacks permission for an operation."""
    pass


class TaskService:
    """Core business logic for task management.

    Enforces authorization rules:
    - Only admins/managers can assign tasks
    - Only task owner or assignee can complete/cancel
    - Only comment author can edit their comments
    """

    def __init__(self):
        self.tasks = TaskRepository()
        self.users = UserRepository()
        self.comments = CommentRepository()

    def create_user(self, name: str, email: str, role: str = "member") -> User:
        existing = self.users.find_by_email(email)
        if existing:
            raise ValueError(f"User with email {email} already exists")
        return self.users.create(name, email, role)

    def create_task(self, title: str, description: str, owner_id: str,
                    priority: str = "medium", tags: List[str] = None) -> Task:
        """Create a new task. Validates owner exists."""
        self.users.get_or_raise(owner_id)
        prio = Priority(priority)
        return self.tasks.create(title, description, owner_id, prio, tags)

    def assign_task(self, task_id: str, assignee_id: str, assigned_by: str) -> Task:
        """Assign a task to a user. Requires manager/admin role."""
        assigner = self.users.get_or_raise(assigned_by)
        if not assigner.can_assign_tasks():
            raise AuthorizationError(
                f"User {assigned_by} ({assigner.role}) cannot assign tasks"
            )
        self.users.get_or_raise(assignee_id)
        task = self.tasks.get_or_raise(task_id)
        task.assign(assignee_id)
        logger.info(f"Task {task_id} assigned to {assignee_id} by {assigned_by}")
        return task

    def complete_task(self, task_id: str, completed_by: str) -> Task:
        """Complete a task. Only owner or assignee can complete."""
        task = self.tasks.get_or_raise(task_id)
        if completed_by not in (task.owner_id, task.assignee_id):
            raise AuthorizationError(
                f"User {completed_by} is neither owner nor assignee of task {task_id}"
            )
        task.complete()
        logger.info(f"Task {task_id} completed by {completed_by}")
        return task

    def cancel_task(self, task_id: str, cancelled_by: str) -> Task:
        """Cancel a task. Only owner can cancel."""
        task = self.tasks.get_or_raise(task_id)
        if cancelled_by != task.owner_id:
            user = self.users.get_or_raise(cancelled_by)
            if not user.is_admin():
                raise AuthorizationError(
                    f"User {cancelled_by} is not the owner of task {task_id}"
                )
        task.cancel()
        logger.info(f"Task {task_id} cancelled by {cancelled_by}")
        return task

    def add_comment(self, task_id: str, author_id: str, content: str) -> Comment:
        """Add a comment to a task."""
        self.tasks.get_or_raise(task_id)
        self.users.get_or_raise(author_id)
        return self.comments.create(task_id, author_id, content)

    def get_task_details(self, task_id: str) -> Dict[str, Any]:
        """Get full task details including comments."""
        task = self.tasks.get_or_raise(task_id)
        comments = self.comments.find_by_task(task_id)
        owner = self.users.get(task.owner_id)
        assignee = self.users.get(task.assignee_id) if task.assignee_id else None

        return {
            "task": task,
            "owner": owner,
            "assignee": assignee,
            "comments": comments,
            "comment_count": len(comments),
        }

    def get_dashboard(self, user_id: str) -> Dict[str, Any]:
        """Get dashboard data for a user."""
        self.users.get_or_raise(user_id)
        owned = self.tasks.find_by_owner(user_id)
        assigned = self.tasks.find_by_assignee(user_id)
        status_counts = self.tasks.count_by_status()

        return {
            "owned_tasks": owned,
            "assigned_tasks": assigned,
            "status_summary": status_counts,
            "total_tasks": self.tasks.count(),
        }

    def search_tasks(self, query: str = None, status: str = None,
                     priority: str = None, tag: str = None) -> List[Task]:
        """Search tasks with optional filters."""
        results = self.tasks.list_all()

        if status:
            results = [t for t in results if t.status == TaskStatus(status)]
        if priority:
            results = [t for t in results if t.priority == Priority(priority)]
        if tag:
            results = [t for t in results if tag in t.tags]
        if query:
            q = query.lower()
            results = [t for t in results
                       if q in t.title.lower() or q in t.description.lower()]

        return results
