"""Notification system for task events.

Provides a pub/sub event system that decouples task state changes
from notification delivery (email, webhook, in-app).
"""

import logging
from typing import List, Dict, Any, Callable, Optional
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .models import Task, User, TaskStatus

logger = logging.getLogger(__name__)


class EventType(Enum):
    TASK_CREATED = "task.created"
    TASK_ASSIGNED = "task.assigned"
    TASK_COMPLETED = "task.completed"
    TASK_CANCELLED = "task.cancelled"
    COMMENT_ADDED = "comment.added"


@dataclass
class Event:
    """Represents a domain event."""
    type: EventType
    payload: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.now)
    source_user_id: Optional[str] = None


class EventBus:
    """Simple in-memory pub/sub event bus.

    Supports synchronous event dispatch with multiple subscribers per event type.
    Subscribers are called in registration order.
    """

    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable[[Event], None]]] = {}
        self._history: List[Event] = []
        self._max_history: int = 1000

    def subscribe(self, event_type: EventType, handler: Callable[[Event], None]) -> None:
        """Register a handler for an event type."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)
        logger.debug(f"Subscribed handler to {event_type.value}")

    def publish(self, event: Event) -> int:
        """Publish an event to all subscribers. Returns number of handlers called."""
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        handlers = self._subscribers.get(event.type, [])
        called = 0
        for handler in handlers:
            try:
                handler(event)
                called += 1
            except Exception as e:
                logger.error(f"Event handler failed for {event.type.value}: {e}")

        logger.info(f"Published {event.type.value}: {called} handler(s) called")
        return called

    def get_history(self, event_type: Optional[EventType] = None,
                    limit: int = 50) -> List[Event]:
        """Get recent events, optionally filtered by type."""
        events = self._history
        if event_type:
            events = [e for e in events if e.type == event_type]
        return events[-limit:]


class NotificationChannel:
    """Base class for notification delivery channels."""

    def send(self, recipient: User, subject: str, body: str) -> bool:
        raise NotImplementedError


class EmailChannel(NotificationChannel):
    """Simulated email notification channel."""

    def __init__(self):
        self.sent: List[Dict[str, str]] = []

    def send(self, recipient: User, subject: str, body: str) -> bool:
        message = {
            "to": recipient.email,
            "subject": subject,
            "body": body,
            "sent_at": datetime.now().isoformat(),
        }
        self.sent.append(message)
        logger.info(f"Email sent to {recipient.email}: {subject}")
        return True


class WebhookChannel(NotificationChannel):
    """Simulated webhook notification channel."""

    def __init__(self, url: str):
        self.url = url
        self.deliveries: List[Dict[str, Any]] = []

    def send(self, recipient: User, subject: str, body: str) -> bool:
        delivery = {
            "url": self.url,
            "payload": {"recipient": recipient.id, "subject": subject, "body": body},
            "delivered_at": datetime.now().isoformat(),
        }
        self.deliveries.append(delivery)
        logger.info(f"Webhook delivered to {self.url}")
        return True


class NotificationService:
    """Orchestrates notification delivery across channels.

    Listens to events from the EventBus and routes notifications
    to the appropriate channels based on event type and user preferences.
    """

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.channels: List[NotificationChannel] = []
        self._register_handlers()

    def add_channel(self, channel: NotificationChannel) -> None:
        self.channels.append(channel)

    def _register_handlers(self) -> None:
        self.event_bus.subscribe(EventType.TASK_ASSIGNED, self._on_task_assigned)
        self.event_bus.subscribe(EventType.TASK_COMPLETED, self._on_task_completed)
        self.event_bus.subscribe(EventType.COMMENT_ADDED, self._on_comment_added)

    def _on_task_assigned(self, event: Event) -> None:
        assignee = event.payload.get("assignee")
        task = event.payload.get("task")
        if assignee and task:
            self._notify(
                recipient=assignee,
                subject=f"Task assigned: {task.title}",
                body=f"You have been assigned to task '{task.title}' ({task.id}).",
            )

    def _on_task_completed(self, event: Event) -> None:
        owner = event.payload.get("owner")
        task = event.payload.get("task")
        if owner and task:
            self._notify(
                recipient=owner,
                subject=f"Task completed: {task.title}",
                body=f"Task '{task.title}' ({task.id}) has been completed.",
            )

    def _on_comment_added(self, event: Event) -> None:
        task_owner = event.payload.get("task_owner")
        comment = event.payload.get("comment")
        if task_owner and comment:
            self._notify(
                recipient=task_owner,
                subject="New comment on your task",
                body=f"A new comment was added: {comment.content[:100]}",
            )

    def _notify(self, recipient: User, subject: str, body: str) -> None:
        """Send notification through all registered channels."""
        for channel in self.channels:
            try:
                channel.send(recipient, subject, body)
            except Exception as e:
                logger.error(f"Channel {type(channel).__name__} failed: {e}")
