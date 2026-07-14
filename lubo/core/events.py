from __future__ import annotations

import copy
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from time import time
from types import MappingProxyType
from typing import Any


logger = logging.getLogger(__name__)


def _freeze_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_payload(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_payload(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_payload(item) for item in value)
    if isinstance(value, (str, bytes, int, float, bool, type(None))):
        return value
    return copy.deepcopy(value)


class RecorderEventType(str, Enum):
    TARGET_ADDED = "target_added"
    TARGET_UPDATED = "target_updated"
    TARGET_REMOVED = "target_removed"
    RESOLVE_STARTED = "resolve_started"
    LIVE_DETECTED = "live_detected"
    OFFLINE_DETECTED = "offline_detected"
    RECORDING_STARTED = "recording_started"
    RECORDING_STOPPED = "recording_stopped"
    RECORDING_FAILED = "recording_failed"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class RecorderEvent:
    type: RecorderEventType
    target_id: str
    message: str = ""
    payload: Mapping[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time)

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _freeze_payload(self.payload))


Subscriber = Callable[[RecorderEvent], None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, subscriber: Subscriber) -> None:
        if subscriber not in self._subscribers:
            self._subscribers.append(subscriber)

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self._subscribers = [item for item in self._subscribers if item != subscriber]

    def publish(self, event: RecorderEvent) -> None:
        for subscriber in list(self._subscribers):
            try:
                subscriber(event)
            except Exception:
                logger.exception("Recorder event subscriber failed")
                continue
