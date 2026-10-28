# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Branch-scoped Pi session events captured for exact evidence recall."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    TextEvidence,
)
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
)
from powercontext.sources.models import Source, SourceMaterialization

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_CONNECTOR_VERSION = "1"

_PI_EVENT_IDENTITY_PREFIX = "pi:"
_PI_SESSION_ARCHIVE_VERSION = 3
_PI_SESSION_HEADER_TYPE = "session"
_PI_CHECKPOINT_VERSION = 1
_PI_UNKNOWN_TOOL_NAME = "unknown"


class PiSessionInput(BaseModel):
    """One Pi v3 session archive offered for branch-scoped capture."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event on the selected branch of a Pi session."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """One captured Pi event addressed by its session and event identity."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class _PiNode:
    """One archive record linked to its parent node."""

    event_id: str
    parent_id: str | None
    record: dict[str, Any]


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Build the stable identity of one captured Pi event."""

    identity = f"{_PI_EVENT_IDENTITY_PREFIX}{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        digest = hashlib.sha256(f"{session_id}\x00{event_id}".encode()).hexdigest()
        identity = f"{_PI_EVENT_IDENTITY_PREFIX}{digest}"
    return identity


def _decode_record(line: str) -> dict[str, Any]:
    """Decode one archive line into a JSON object."""

    try:
        record = json.loads(line)
    except json.JSONDecodeError as error:
        raise ValueError(f"Pi session archive contains invalid JSON: {error}") from error  # noqa: TRY003
    if not isinstance(record, dict):
        raise ValueError("Pi session records must be JSON objects")  # noqa: TRY003, TRY004
    return record


def _header_session_id(header: dict[str, Any]) -> str:
    """Validate the leading session record and return its identifier."""

    if header.get("type") != _PI_SESSION_HEADER_TYPE:
        raise ValueError("Pi session archive must start with a session record")  # noqa: TRY003
    if header.get("version") != _PI_SESSION_ARCHIVE_VERSION:
        raise ValueError("Pi session archive must use version 3")  # noqa: TRY003
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("Pi session header must declare a non-empty id")  # noqa: TRY003
    return session_id


def _node_event_id(record: dict[str, Any]) -> str:
    """Read the unique identifier of one archive record."""

    event_id = record.get("id")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("Pi session records must declare a non-empty id")  # noqa: TRY003
    return event_id


def _node_parent_id(record: dict[str, Any]) -> str | None:
    """Read the parent reference of one archive record."""

    parent_id = record.get("parentId")
    if parent_id is None:
        return None
    if not isinstance(parent_id, str) or not parent_id:
        raise ValueError("Pi session parentId must be a non-empty string or null")  # noqa: TRY003
    return parent_id


def _decode_archive(content: bytes) -> tuple[str, tuple[_PiNode, ...]]:
    """Decode one UTF-8 Pi v3 JSONL archive into its session id and ordered nodes."""

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session content must be valid UTF-8") from error  # noqa: TRY003
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Pi session archive must contain a session header")  # noqa: TRY003
    session_id = _header_session_id(_decode_record(lines[0]))
    nodes: list[_PiNode] = []
    seen: set[str] = set()
    for line in lines[1:]:
        record = _decode_record(line)
        event_id = _node_event_id(record)
        parent_id = _node_parent_id(record)
        if event_id in seen:
            raise ValueError(f"Pi session event {event_id!r} is duplicated")  # noqa: TRY003
        if parent_id is not None and parent_id not in seen:
            raise ValueError(f"Pi session event {event_id!r} references an unknown parent {parent_id!r}")  # noqa: TRY003
        seen.add(event_id)
        nodes.append(_PiNode(event_id=event_id, parent_id=parent_id, record=record))
    return session_id, tuple(nodes)


def _selected_chain(nodes: tuple[_PiNode, ...], leaf_id: str | None) -> tuple[_PiNode, ...]:
    """Return the ancestor chain of the selected leaf ordered from root to leaf."""

    by_id = {node.event_id: node for node in nodes}
    if leaf_id is None:
        if not nodes:
            raise ValueError("Pi session archive must contain at least one event")  # noqa: TRY003
        target = nodes[-1]
    else:
        target = by_id.get(leaf_id)
        if target is None:
            raise ValueError(f"Pi session leaf {leaf_id!r} was not found")  # noqa: TRY003
    chain: list[_PiNode] = []
    current: _PiNode | None = target
    while current is not None:
        chain.append(current)
        if current.parent_id is None:
            break
        parent = by_id.get(current.parent_id)
        if parent is None:
            raise ValueError(f"Pi session event {current.event_id!r} references an unknown parent")  # noqa: TRY003
        current = parent
    chain.reverse()
    return tuple(chain)


def _tool_call_text(block: dict[str, Any]) -> str:
    """Render one tool call block as visible evidence."""

    name = block.get("name")
    call_id = block.get("id")
    name_text = name if isinstance(name, str) and name else _PI_UNKNOWN_TOOL_NAME
    call_text = call_id if isinstance(call_id, str) and call_id else _PI_UNKNOWN_TOOL_NAME
    arguments = block.get("arguments")
    payload = json.dumps(
        arguments if arguments is not None else {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"[tool_call {name_text} {call_text}] {payload}"


def _visible_text(content: Any) -> str:
    """Return the visible text of a string payload or a block list."""

    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        elif block_type == "toolCall":
            parts.append(_tool_call_text(block))
    return "\n".join(parts)


def _tool_result_body(message: dict[str, Any]) -> str:
    """Render one tool result message as visible evidence."""

    tool_name = message.get("toolName")
    call_id = message.get("toolCallId")
    name_text = tool_name if isinstance(tool_name, str) and tool_name else _PI_UNKNOWN_TOOL_NAME
    call_text = call_id if isinstance(call_id, str) and call_id else _PI_UNKNOWN_TOOL_NAME
    return f"[tool_result {name_text} {call_text}] {_visible_text(message.get('content'))}"


def _bash_execution_body(message: dict[str, Any]) -> str:
    """Render one bash execution message as visible evidence."""

    command = message.get("command")
    output = message.get("output")
    command_text = command if isinstance(command, str) else ""
    output_text = output if isinstance(output, str) else ""
    if not command_text and not output_text:
        return ""
    return f"$ {command_text}\n{output_text}"


def _message_body(record: dict[str, Any]) -> tuple[str, str]:
    """Return the role and visible body of one message record."""

    message = record.get("message")
    if not isinstance(message, dict):
        return "", ""
    role = message.get("role")
    if not isinstance(role, str) or not role:
        return "", ""
    if role == "toolResult":
        return role, _tool_result_body(message)
    if role == "bashExecution":
        return role, _bash_execution_body(message)
    return role, _visible_text(message.get("content"))


def _summary_body(record: dict[str, Any]) -> str:
    """Return the summary of one compaction or branch summary record."""

    summary = record.get("summary")
    return summary if isinstance(summary, str) else ""


def _visible_event(session_id: str, node: _PiNode) -> PiEventInput | None:
    """Build the visible event of one archive node, or None when it carries no text."""

    event_type = node.record.get("type")
    if event_type == "message":
        role, body = _message_body(node.record)
    elif event_type == "compaction":
        role, body = "compaction", _summary_body(node.record)
    elif event_type == "branch_summary":
        role, body = "branch_summary", _summary_body(node.record)
    elif event_type == "custom_message":
        role, body = "custom", _visible_text(node.record.get("content"))
    else:
        return None
    if not body:
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=node.event_id,
        parent_id=node.parent_id,
        event_type=str(event_type),
        role=role,
        content=f"{role}: {body}",
        record=node.record,
    )


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Parse one Pi session archive into the visible events of its selected branch."""

    session_id, nodes = _decode_archive(value.content)
    chain = _selected_chain(nodes, value.leaf_id)
    events = []
    for node in chain:
        event = _visible_event(session_id, node)
        if event is not None:
            events.append(event)
    return tuple(events)


class PiEventSourceAdapter:
    """Resolve and read the branch-scoped Pi event Source."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    """Expose one captured Pi event as standard textual evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_ADAPTER = PiEventSourceAdapter()
PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PI_EVENT_SOURCE_ADAPTER,
    projections=(PiEventTextEvidenceProjection(),),
)


def _record_digest(record: dict[str, Any]) -> str:
    """Return the stable digest of one complete archive record."""

    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"


def _imported_events(checkpoint: JsonValue | None, session_id: str) -> dict[str, dict[str, JsonValue]]:
    """Read the already imported events recorded in one durable checkpoint."""

    if not isinstance(checkpoint, dict) or checkpoint.get("session_id") != session_id:
        return {}
    events = checkpoint.get("events")
    if not isinstance(events, dict):
        return {}
    return {event_id: entry for event_id, entry in events.items() if isinstance(entry, dict)}


def _reject_rewritten_events(
    imported: dict[str, dict[str, JsonValue]],
    nodes: tuple[_PiNode, ...],
) -> None:
    """Refuse a run when an already imported event was rewritten in the archive."""

    by_id = {node.event_id: node for node in nodes}
    for event_id, entry in imported.items():
        node = by_id.get(event_id)
        if node is None:
            continue
        if entry.get("digest") != _record_digest(node.record):
            raise ValueError(f"Pi session event {event_id!r} was rewritten after it was imported")  # noqa: TRY003


def _build_checkpoint(
    session_id: str,
    imported: dict[str, dict[str, JsonValue]],
    events: tuple[PiEventInput, ...],
) -> dict[str, JsonValue]:
    """Build the durable checkpoint describing every imported event."""

    merged = dict(imported)
    for event in events:
        merged[event.event_id] = {
            "event_id": event.event_id,
            "parent_id": event.parent_id,
            "event_type": event.event_type,
            "role": event.role,
            "content": event.content,
            "digest": _record_digest(event.record),
        }
    return {
        "version": _PI_CHECKPOINT_VERSION,
        "session_id": session_id,
        "events": merged,
    }


class PiSessionConnector:
    """Import the visible events of one Pi branch incrementally."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SESSION_CONNECTOR_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        session_id, nodes = _decode_archive(self.path.read_bytes())
        chain = _selected_chain(nodes, self.leaf_id)
        events = tuple(event for event in (_visible_event(session_id, node) for node in chain) if event is not None)
        imported = _imported_events(session.checkpoint, session_id)
        _reject_rewritten_events(imported, nodes)
        for event in events:
            if event.event_id in imported:
                continue
            await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint=_build_checkpoint(session_id, imported, events),
        )
