"""Task state persistence backed by PowerContext.

The state of a development task (what is done, what is still open, which
verification results exist) is stored inside PowerContext:

* every save writes a **content Source** into a dedicated Scope.  The
  content is the JSON document of the task state, so arbitrary fields are
  supported.
* every save additionally records a **Task Outcome** through the PowerContext
  work API.  That gives a structured, inspectable view of the progress
  (status, checks, remaining work).

The PowerContext storage backend is a SQLite file, therefore the state
survives a restart of the context module: a later run opens the same file,
reads the Scope and the Source journal back and continues from the stored
state.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config

__all__ = ["PowerContextStateStore", "StateError", "powercontext_version"]

SOURCE_TYPE = "content"


class StateError(RuntimeError):
    """Raised when the task state cannot be read or written."""


def powercontext_version() -> str:
    """Return the installed PowerContext version."""
    try:
        from importlib.metadata import version

        return version("powercontext")
    except Exception:  # pragma: no cover - depends on the environment
        return "unknown"


@dataclass
class StateResult:
    """The outcome of one state operation."""

    scope_id: str
    source_id: str
    position: int
    detail: dict[str, Any]


class PowerContextStateStore:
    """Read and write the task state with PowerContext."""

    def __init__(self, config: Config):
        self.config = config

    # -- runtime plumbing ------------------------------------------------
    def _config(self):
        from powercontext.builtin.persistence.sqlite import SQLiteConfig
        from powercontext.builtin.runtime import BuiltinConfig

        self.config.ensure_state_dir()
        database_url = f"sqlite+aiosqlite:///{Path(self.config.state_db).as_posix()}"
        return BuiltinConfig(database=SQLiteConfig(url=database_url))

    def _run(self, coroutine):
        return asyncio.run(coroutine)

    async def _scope_id(self, runtime) -> str:
        from powercontext.builtin.scope import ScopeDraft

        draft = ScopeDraft(
            title=self.config.scope_title,
            summary="Task state of the SDD context module",
            idempotency_key=self.config.scope_key,
        )
        descriptor = await runtime.scopes.create(draft)
        return str(descriptor.scope_id)

    # -- public API -------------------------------------------------------
    def save(self, task_id: str, state: dict) -> StateResult:
        """Persist ``state`` for ``task_id`` and return the stored reference."""
        return self._run(self._save(task_id, state))

    def load(self, task_id: str) -> dict | None:
        """Return the stored state of ``task_id`` or ``None``."""
        return self._run(self._load(task_id))

    def list_tasks(self) -> list[dict]:
        """Return every task state that is currently stored."""
        return self._run(self._list_tasks())

    def describe(self) -> dict:
        """Describe the PowerContext setup (version, file, scope)."""
        return {
            "powercontext_version": powercontext_version(),
            "state_db": str(self.config.state_db),
            "scope_key": self.config.scope_key,
            "scope_title": self.config.scope_title,
        }

    def reset(self) -> dict:
        """Remove the local PowerContext storage so that a run starts empty.

        The PowerContext database is a local file; deleting it removes the
        Scope and every stored task state.  This is the "start from an empty
        state" reset of the validation sample.
        """
        removed = []
        for path in (Path(self.config.state_db), Path(self.config.scheduler_db)):
            if path.exists():
                path.unlink()
                removed.append(str(path))
        return {"removed": removed, "state_db": str(self.config.state_db)}

    # -- implementations --------------------------------------------------
    async def _save(self, task_id: str, state: dict) -> StateResult:
        from powercontext.builtin.runtime import open_builtin_runtime
        from powercontext.builtin.runtime.models import CaptureSource

        payload = json.dumps(state, ensure_ascii=False, sort_keys=True)
        config = self._config()
        async with open_builtin_runtime(config, scheduler_path=str(self.config.scheduler_db)) as runtime:
            scope_id = await self._scope_id(runtime)
            source_id, receipt = await self._capture_state(runtime, scope_id, task_id, payload, state)
            outcome_detail = await self._record_outcome(runtime, scope_id, task_id, state, source_id)
        return StateResult(
            scope_id=scope_id,
            source_id=source_id,
            position=int(receipt.sequence),
            detail=outcome_detail,
        )

    async def _capture_state(self, runtime, scope_id: str, task_id: str, payload: str, state: dict):
        """Write the state document; every save creates a new Source revision."""
        from powercontext.builtin.runtime.models import CaptureSource

        base_id = f"sdd-task-{task_id}"
        metadata = {
            "kind": "sdd.task_state",
            "task_id": task_id,
            "status": str(state.get("status", "unknown")),
            "schema": "sdd.task-state.v1",
        }
        last_error: Exception | None = None
        for version in range(1, 100):
            source_id = base_id if version == 1 else f"{base_id}-v{version}"
            try:
                receipt = await runtime.sources.for_scope(scope_id).capture(
                    CaptureSource(source_id=source_id, content=payload, metadata=metadata)
                )
                return source_id, receipt
            except Exception as exc:
                if type(exc).__name__ != "SourceConflictError":
                    raise
                last_error = exc
        raise StateError(f"could not store the task state: {last_error}")

    async def _record_outcome(self, runtime, scope_id: str, task_id: str, state: dict, source_id: str) -> dict:
        """Store the structured task outcome next to the raw state."""
        try:
            from powercontext.builtin.work import RecordTaskOutcome, TaskCheck, TaskOutcome

            receipt = await self._record_outcome_record(runtime, scope_id, task_id, state, source_id)
            return {
                "work_outcome_source_ref": str(receipt.source_ref),
                "work_outcome_position": int(receipt.position),
                "work_outcome_kind": str(receipt.kind),
            }
        except Exception as exc:  # pragma: no cover - depends on the PowerContext build
            return {"work_outcome_error": f"{type(exc).__name__}: {exc}"}

    async def _record_outcome_record(self, runtime, scope_id: str, task_id: str, state: dict, source_id: str):
        """Store the structured task outcome; a new revision per save."""
        from powercontext.builtin.work import RecordTaskOutcome, TaskCheck, TaskOutcome, WorkClaim

        checks = []
        for check in state.get("verification", []) or []:
            if not isinstance(check, dict):
                continue
            checks.append(
                TaskCheck(
                    name=str(check.get("name", "check"))[:512],
                    status=str(check.get("status", "skipped")),
                    details=str(check.get("details", ""))[:512] or None,
                    basis="declared",
                )
            )
        observations = tuple(
            WorkClaim(text=f"{kind}: {value}"[:2048])
            for kind, value in _observation_items(state)
        ) or (WorkClaim(text=f"task {task_id} updated"[:2048]),)
        outcome = TaskOutcome(
            objective=str(state.get("objective", task_id))[:2048],
            status=_outcome_status(state.get("status", "unknown")),
            summary=str(state.get("summary", ""))[:2048] or f"state of {task_id}",
            observations=observations[:16],
            checks=tuple(checks)[:16],
            remaining_work=tuple(str(item)[:512] for item in state.get("todo", []) or [])[:16],
        )
        base_id = f"{source_id}-outcome"
        last_error: Exception | None = None
        for version in range(1, 100):
            outcome_id = base_id if version == 1 else f"{base_id}-v{version}"
            try:
                return await runtime.work.for_scope(scope_id).record_outcome(
                    RecordTaskOutcome(source_id=outcome_id, outcome=outcome)
                )
            except Exception as exc:
                if type(exc).__name__ != "SourceConflictError":
                    raise
                last_error = exc
        raise StateError(f"could not store the task outcome: {last_error}")

    async def _load(self, task_id: str) -> dict | None:
        from powercontext.builtin.runtime import open_builtin_runtime

        config = self._config()
        async with open_builtin_runtime(config, scheduler_path=str(self.config.scheduler_db)) as runtime:
            scope_id = await self._scope_id(runtime)
            records = await self._list_sources(runtime, scope_id)
        wanted = f"sdd-task-{task_id}"
        candidates = []
        for record in records:
            source_id = str(record.source_id)
            if source_id == wanted or (source_id.startswith(wanted + "-v") and source_id[len(wanted) + 2:].isdigit()):
                candidates.append(record)
        if not candidates:
            return None
        latest = max(candidates, key=lambda record: _version_of(str(record.source_id), wanted))
        content = latest.content
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except ValueError:
                pass
        if not isinstance(content, dict):
            content = {"raw": content}
        content["_powercontext"] = {
            "scope_id": scope_id,
            "source_id": str(latest.source_id),
            "position": int(latest.position),
            "content_digest": str(getattr(latest, "content_digest", "")),
        }
        return content

    async def _list_sources(self, runtime, scope_id: str) -> list:
        """Return every content Source of the scope, following the cursor."""
        records: list = []
        cursor: str | None = None
        while True:
            page = await runtime.records.for_scope(scope_id).list_sources(limit=100, cursor=cursor)
            records.extend(list(getattr(page, "items", []) or []))
            cursor = getattr(page, "next_cursor", None)
            if not cursor:
                break
        return records

    async def _list_tasks(self) -> list[dict]:
        from powercontext.builtin.runtime import open_builtin_runtime

        config = self._config()
        async with open_builtin_runtime(config, scheduler_path=str(self.config.scheduler_db)) as runtime:
            scope_id = await self._scope_id(runtime)
            records = await self._list_sources(runtime, scope_id)
        tasks: list[dict] = []
        latest: dict[str, dict] = {}
        for record in records:
            source_id = str(record.source_id)
            if not source_id.startswith("sdd-task-"):
                continue
            if source_id.endswith("-outcome") or "-outcome-v" in source_id:
                continue
            task_id = _task_id_of(source_id)
            content = record.content
            if isinstance(content, str):
                try:
                    content = json.loads(content)
                except ValueError:
                    content = {"raw": content}
            entry = {
                "task_id": task_id,
                "source_id": source_id,
                "position": int(record.position),
                "state": content if isinstance(content, dict) else {"raw": content},
            }
            current = latest.get(task_id)
            if current is None or _version_of(source_id, f"sdd-task-{task_id}") >= _version_of(
                str(current["source_id"]), f"sdd-task-{task_id}"
            ):
                latest[task_id] = entry
        tasks.extend(latest.values())
        tasks.sort(key=lambda item: item["position"])
        return tasks


def _observation_items(state: dict) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for entry in state.get("done", []) or []:
        items.append(("done", str(entry)))
    for entry in state.get("todo", []) or []:
        items.append(("todo", str(entry)))
    return items


_OUTCOME_STATUS = {
    "completed": "succeeded",
    "succeeded": "succeeded",
    "in_progress": "partial",
    "partial": "partial",
    "failed": "failed",
    "blocked": "blocked",
    "cancelled": "cancelled",
    "unknown": "unknown",
}


def _outcome_status(status: object) -> str:
    """Map the module status onto a PowerContext task outcome status."""
    return _OUTCOME_STATUS.get(str(status), "unknown")


def _version_of(source_id: str, base_id: str) -> int:
    """Return the revision number encoded in ``source_id``."""
    if source_id == base_id:
        return 1
    suffix = source_id[len(base_id) + 2:] if source_id.startswith(base_id + "-v") else ""
    return int(suffix) if suffix.isdigit() else 1


def _task_id_of(source_id: str) -> str:
    """Return the task id encoded in a state Source id."""
    body = source_id[len("sdd-task-"):]
    if "-v" in body:
        head, _, tail = body.rpartition("-v")
        if tail.isdigit():
            body = head
    return body
