"""Audit storage. In-memory (bounded) plus an append-only JSONL event log for the audit trail.
Round 3 swaps this for SQLite so the governance dashboard can query history."""
from __future__ import annotations

import json
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

from .schemas import Audit, AuditEvent


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AuditStore:
    def __init__(self, log_path: Path | None = None, max_items: int = 500) -> None:
        self._items: "OrderedDict[str, Audit]" = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_items
        self._log_path = log_path

    def put(self, audit: Audit) -> None:
        with self._lock:
            self._items[audit.audit_id] = audit
            self._items.move_to_end(audit.audit_id)
            while len(self._items) > self._max:
                self._items.popitem(last=False)

    def get(self, audit_id: str, workspace_id: str) -> Audit | None:
        with self._lock:
            audit = self._items.get(audit_id)
        # Workspace check keeps one judge's audits private from another's.
        if audit is None or audit.workspace_id != workspace_id:
            return None
        return audit

    def log(self, audit: Audit, type_: str, finding_id: str | None = None, detail: str = "") -> AuditEvent:
        event = AuditEvent(ts=now_iso(), type=type_, finding_id=finding_id, detail=detail)
        audit.events.append(event)
        if self._log_path:
            try:
                self._log_path.parent.mkdir(parents=True, exist_ok=True)
                with self._log_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"audit_id": audit.audit_id, **event.model_dump()}) + "\n")
            except OSError:
                pass
        return event
