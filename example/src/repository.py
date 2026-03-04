"""In-memory repository layer for persisting domain entities."""

import logging
from typing import Dict, List, Optional
from datetime import datetime

from .models import Task, User, Comment, TaskStatus, Priority

logger = logging.getLogger(__name__)


class TaskRepository:
    """In-memory store for tasks with query capabilities."""

    def __init__(self):
        self._tasks: Dict[str, Task] = {}
        self._counter: int = 0

    def create(self, title: str, description: str, owner_id: str,
               priority: Priority = Priority.MEDIUM, tags: List[str] = None) -> Task:
        """Create and store a new task."""
        self._counter += 1
        task = Task(
            id=f"task-{self._counter}",
            title=title,
            description=description,
            owner_id=owner_id,
            priority=priority,
            tags=tags or [],
        )
        self._tasks[task.id] = task
        logger.info(f"Created task {task.id}: {title}")
        return task

    def get(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def get_or_raise(self, task_id: str) -> Task:
        task = self.get(task_id)
        if task is None:
            raise KeyError(f"Task not found: {task_id}")
        return task

    def list_all(self) -> List[Task]:
        return list(self._tasks.values())

    def find_by_status(self, status: TaskStatus) -> List[Task]:
        return [t for t in self._tasks.values() if t.status == status]

    def find_by_owner(self, owner_id: str) -> List[Task]:
        return [t for t in self._tasks.values() if t.owner_id == owner_id]

    def find_by_assignee(self, assignee_id: str) -> List[Task]:
        return [t for t in self._tasks.values() if t.assignee_id == assignee_id]

    def find_by_priority(self, priority: Priority) -> List[Task]:
        return [t for t in self._tasks.values() if t.priority == priority]

    def find_by_tag(self, tag: str) -> List[Task]:
        return [t for t in self._tasks.values() if tag in t.tags]

    def delete(self, task_id: str) -> bool:
        if task_id in self._tasks:
            del self._tasks[task_id]
            logger.info(f"Deleted task {task_id}")
            return True
        return False

    def count(self) -> int:
        return len(self._tasks)

    def count_by_status(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for task in self._tasks.values():
            key = task.status.value
            counts[key] = counts.get(key, 0) + 1
        return counts


class UserRepository:
    """In-memory store for users."""

    def __init__(self):
        self._users: Dict[str, User] = {}
        self._counter: int = 0

    def create(self, name: str, email: str, role: str = "member") -> User:
        self._counter += 1
        user = User(id=f"user-{self._counter}", name=name, email=email, role=role)
        self._users[user.id] = user
        logger.info(f"Created user {user.id}: {name}")
        return user

    def get(self, user_id: str) -> Optional[User]:
        return self._users.get(user_id)

    def get_or_raise(self, user_id: str) -> User:
        user = self.get(user_id)
        if user is None:
            raise KeyError(f"User not found: {user_id}")
        return user

    def find_by_email(self, email: str) -> Optional[User]:
        for user in self._users.values():
            if user.email == email:
                return user
        return None

    def list_all(self) -> List[User]:
        return list(self._users.values())


class CommentRepository:
    """In-memory store for task comments."""

    def __init__(self):
        self._comments: Dict[str, Comment] = {}
        self._counter: int = 0

    def create(self, task_id: str, author_id: str, content: str) -> Comment:
        self._counter += 1
        comment = Comment(
            id=f"comment-{self._counter}",
            task_id=task_id,
            author_id=author_id,
            content=content,
        )
        self._comments[comment.id] = comment
        return comment

    def find_by_task(self, task_id: str) -> List[Comment]:
        return [c for c in self._comments.values() if c.task_id == task_id]

    def delete(self, comment_id: str) -> bool:
        if comment_id in self._comments:
            del self._comments[comment_id]
            return True
        return False
