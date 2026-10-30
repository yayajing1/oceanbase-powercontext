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

"""Source withdrawal lifecycle: registrations, events, and availability rules."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.lifecycle import (
    LifecycleEventRecord,
    LifecycleFamily,
    LifecycleNode,
    LifecyclePolicy,
)
from powercontext.sources import SourceRef

LIFECYCLE_PURPOSE_GENERATION = "generation"
LIFECYCLE_PURPOSE_AUDIT = "audit"
LIFECYCLE_PURPOSES: tuple[str, ...] = (LIFECYCLE_PURPOSE_GENERATION, LIFECYCLE_PURPOSE_AUDIT)

MAX_LIFECYCLE_NODE_ID_LENGTH = 256
MAX_LIFECYCLE_REASON_LENGTH = 4096
MAX_LIFECYCLE_MEMORY_REASON_LENGTH = 512

LifecycleReference = SourceRef | MemoryCitation | ArtifactRef


class SourceLifecycleError(ValueError):
    """Report a rejected Source lifecycle registration, event, or resolution."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code if detail is None else f"{code}: {detail}")


class SourceLifecycleEvent(BaseModel):
    """One validated availability change for one registered Source."""

    model_config = ConfigDict(extra="forbid")

    node_id: Annotated[str, Field(min_length=1, max_length=MAX_LIFECYCLE_NODE_ID_LENGTH)]
    reason: Annotated[str, Field(min_length=1, max_length=MAX_LIFECYCLE_REASON_LENGTH)]
    revision: Annotated[int, Field(gt=0)]
    sequence: Annotated[int, Field(gt=0)]
    available: bool

    @field_validator("node_id", "reason")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must contain at least one non-whitespace character")  # noqa: TRY003
        return value


def encode_reference(reference: LifecycleReference) -> str:
    """Encode one registered reference as canonical JSON."""

    if isinstance(reference, SourceRef):
        kind = "source"
    elif isinstance(reference, MemoryCitation):
        kind = "memory"
    elif isinstance(reference, ArtifactRef):
        kind = "topic"
    else:
        raise SourceLifecycleError("unsupported-reference", type(reference).__name__)
    payload = {"kind": kind, "value": reference.model_dump(mode="json")}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def decode_reference(payload: str) -> LifecycleReference:
    """Decode one registered reference from its canonical JSON form."""

    try:
        decoded = json.loads(payload)
        kind = decoded["kind"]
        value = decoded["value"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise SourceLifecycleError("invalid-reference", payload) from error
    if kind == "source":
        return SourceRef.model_validate(value)
    if kind == "memory":
        return MemoryCitation.model_validate(value)
    if kind == "topic":
        return ArtifactRef.model_validate(value)
    raise SourceLifecycleError("invalid-reference", str(kind))


def validate_policy(policy: str) -> LifecyclePolicy:
    """Accept only the two documented dependency policies."""

    if policy == "all":
        return "all"
    if policy == "any":
        return "any"
    raise SourceLifecycleError("unsupported-policy", policy)


def validate_parents(parents: Sequence[str]) -> tuple[str, ...]:
    """Reject empty, blank, and repeated parent identities."""

    validated: list[str] = []
    for parent in parents:
        if not isinstance(parent, str) or not parent.strip():
            raise SourceLifecycleError("invalid-parent", repr(parent))
        if parent in validated:
            raise SourceLifecycleError("repeated-parent", parent)
        validated.append(parent)
    if not validated:
        raise SourceLifecycleError("missing-parent")
    return tuple(validated)


def compute_availability(
    nodes: Sequence[LifecycleNode],
    overrides: Mapping[str, bool],
) -> dict[str, bool]:
    """Recompute every derived node availability from the current parent states.

    Registration order is a topological order because a parent must be registered
    before its child, so one pass over the ordinal reproduces the whole graph.
    """

    availability = {node.node_id: node.available for node in nodes}
    availability.update(overrides)
    for node in sorted(nodes, key=lambda item: item.ordinal):
        if node.family == "source":
            continue
        support = [availability.get(parent, False) for parent in node.parents]
        availability[node.node_id] = all(support) if node.policy == "all" else any(support)
    return availability


def available_node_ids(availability: Mapping[str, bool]) -> list[str]:
    """Return every available node identity in node-number order."""

    return sorted(node_id for node_id, available in availability.items() if available)


def event_record(event: SourceLifecycleEvent) -> LifecycleEventRecord:
    """Project one validated event onto its persisted record."""

    return LifecycleEventRecord(
        node_id=event.node_id,
        revision=event.revision,
        sequence=event.sequence,
        available=event.available,
        reason=event.reason,
    )


def same_event(record: LifecycleEventRecord, event: SourceLifecycleEvent) -> bool:
    """Return whether a recorded event carries exactly the same content."""

    return (
        record.revision == event.revision
        and record.available == event.available
        and record.reason == event.reason
    )


def memory_reason(reason: str) -> str:
    """Bound one audit reason to the size the Memory change record accepts."""

    return reason[:MAX_LIFECYCLE_MEMORY_REASON_LENGTH]


def node_binding(node: LifecycleNode) -> tuple[Any, ...]:
    """Return the comparable binding of one registered node."""

    return (node.family, node.policy, node.parents, node.reference, node.revision)


__all__ = [
    "LIFECYCLE_PURPOSES",
    "LIFECYCLE_PURPOSE_AUDIT",
    "LIFECYCLE_PURPOSE_GENERATION",
    "MAX_LIFECYCLE_NODE_ID_LENGTH",
    "MAX_LIFECYCLE_REASON_LENGTH",
    "LifecycleFamily",
    "LifecyclePolicy",
    "LifecycleReference",
    "SourceLifecycleError",
    "SourceLifecycleEvent",
    "available_node_ids",
    "compute_availability",
    "decode_reference",
    "encode_reference",
    "event_record",
    "memory_reason",
    "node_binding",
    "same_event",
    "validate_parents",
    "validate_policy",
]
