"""REST API layer — HTTP handlers for task management.

Uses a simple WSGI-compatible approach for demonstration.
In production, this would use FastAPI, Flask, or similar.
"""

import json
import logging
from typing import Dict, Any, Tuple, Optional
from dataclasses import asdict
from datetime import datetime

from .service import TaskService, AuthorizationError
from .models import Task, User, Comment

logger = logging.getLogger(__name__)


class JSONEncoder:
    """Custom encoder for domain objects."""

    @staticmethod
    def encode(obj: Any) -> str:
        return json.dumps(obj, default=JSONEncoder._default, indent=2)

    @staticmethod
    def _default(o: Any) -> Any:
        if isinstance(o, datetime):
            return o.isoformat()
        if hasattr(o, '__dataclass_fields__'):
            return asdict(o)
        if hasattr(o, 'value'):  # Enum
            return o.value
        raise TypeError(f"Object of type {type(o)} is not JSON serializable")


class APIResponse:
    """Standardized API response wrapper."""

    @staticmethod
    def success(data: Any, status: int = 200) -> Tuple[Dict, int]:
        return {"status": "ok", "data": data}, status

    @staticmethod
    def error(message: str, status: int = 400) -> Tuple[Dict, int]:
        return {"status": "error", "message": message}, status

    @staticmethod
    def not_found(resource: str) -> Tuple[Dict, int]:
        return APIResponse.error(f"{resource} not found", 404)


class TaskAPI:
    """HTTP API handler for task operations.

    Routes:
        POST   /tasks              - Create task
        GET    /tasks              - List/search tasks
        GET    /tasks/:id          - Get task details
        PUT    /tasks/:id/assign   - Assign task
        PUT    /tasks/:id/complete - Complete task
        PUT    /tasks/:id/cancel   - Cancel task
        POST   /tasks/:id/comments - Add comment
        GET    /dashboard/:user_id - User dashboard
    """

    def __init__(self):
        self.service = TaskService()
        self.encoder = JSONEncoder()

    def handle_request(self, method: str, path: str,
                       body: Optional[Dict] = None,
                       headers: Optional[Dict] = None) -> Tuple[Dict, int]:
        """Route incoming requests to appropriate handlers."""
        try:
            parts = path.strip("/").split("/")

            if parts[0] == "tasks":
                if len(parts) == 1:
                    if method == "POST":
                        return self._create_task(body or {})
                    elif method == "GET":
                        return self._list_tasks(body or {})

                elif len(parts) == 2:
                    task_id = parts[1]
                    if method == "GET":
                        return self._get_task(task_id)

                elif len(parts) == 3:
                    task_id = parts[1]
                    action = parts[2]
                    if action == "assign" and method == "PUT":
                        return self._assign_task(task_id, body or {})
                    elif action == "complete" and method == "PUT":
                        return self._complete_task(task_id, body or {})
                    elif action == "cancel" and method == "PUT":
                        return self._cancel_task(task_id, body or {})
                    elif action == "comments" and method == "POST":
                        return self._add_comment(task_id, body or {})

            elif parts[0] == "dashboard" and len(parts) == 2:
                if method == "GET":
                    return self._get_dashboard(parts[1])

            elif parts[0] == "users":
                if len(parts) == 1 and method == "POST":
                    return self._create_user(body or {})

            return APIResponse.error("Route not found", 404)

        except AuthorizationError as e:
            logger.warning(f"Authorization error: {e}")
            return APIResponse.error(str(e), 403)
        except KeyError as e:
            return APIResponse.not_found(str(e))
        except ValueError as e:
            return APIResponse.error(str(e), 400)
        except Exception as e:
            logger.error(f"Internal error: {e}", exc_info=True)
            return APIResponse.error("Internal server error", 500)

    def _create_user(self, body: Dict) -> Tuple[Dict, int]:
        user = self.service.create_user(
            name=body["name"],
            email=body["email"],
            role=body.get("role", "member"),
        )
        return APIResponse.success(asdict(user), 201)

    def _create_task(self, body: Dict) -> Tuple[Dict, int]:
        task = self.service.create_task(
            title=body["title"],
            description=body.get("description", ""),
            owner_id=body["owner_id"],
            priority=body.get("priority", "medium"),
            tags=body.get("tags", []),
        )
        return APIResponse.success(asdict(task), 201)

    def _list_tasks(self, params: Dict) -> Tuple[Dict, int]:
        tasks = self.service.search_tasks(
            query=params.get("query"),
            status=params.get("status"),
            priority=params.get("priority"),
            tag=params.get("tag"),
        )
        return APIResponse.success([asdict(t) for t in tasks])

    def _get_task(self, task_id: str) -> Tuple[Dict, int]:
        details = self.service.get_task_details(task_id)
        return APIResponse.success({
            "task": asdict(details["task"]),
            "owner": asdict(details["owner"]) if details["owner"] else None,
            "assignee": asdict(details["assignee"]) if details["assignee"] else None,
            "comments": [asdict(c) for c in details["comments"]],
            "comment_count": details["comment_count"],
        })

    def _assign_task(self, task_id: str, body: Dict) -> Tuple[Dict, int]:
        task = self.service.assign_task(
            task_id=task_id,
            assignee_id=body["assignee_id"],
            assigned_by=body["assigned_by"],
        )
        return APIResponse.success(asdict(task))

    def _complete_task(self, task_id: str, body: Dict) -> Tuple[Dict, int]:
        task = self.service.complete_task(
            task_id=task_id,
            completed_by=body["completed_by"],
        )
        return APIResponse.success(asdict(task))

    def _cancel_task(self, task_id: str, body: Dict) -> Tuple[Dict, int]:
        task = self.service.cancel_task(
            task_id=task_id,
            cancelled_by=body["cancelled_by"],
        )
        return APIResponse.success(asdict(task))

    def _add_comment(self, task_id: str, body: Dict) -> Tuple[Dict, int]:
        comment = self.service.add_comment(
            task_id=task_id,
            author_id=body["author_id"],
            content=body["content"],
        )
        return APIResponse.success(asdict(comment), 201)

    def _get_dashboard(self, user_id: str) -> Tuple[Dict, int]:
        dashboard = self.service.get_dashboard(user_id)
        return APIResponse.success({
            "owned_tasks": [asdict(t) for t in dashboard["owned_tasks"]],
            "assigned_tasks": [asdict(t) for t in dashboard["assigned_tasks"]],
            "status_summary": dashboard["status_summary"],
            "total_tasks": dashboard["total_tasks"],
        })
