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

"""Relational persistence for Source lifecycle registrations and events."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.errors import InvalidStoredColumnError
from powercontext.builtin.persistence.tables import (
    SOURCE_LIFECYCLE_EVENTS_TABLE,
    SOURCE_LIFECYCLE_NODES_TABLE,
)

LifecycleFamily = Literal["source", "memory", "topic"]
LifecyclePolicy = Literal["all", "any"]

LIFECYCLE_FAMILIES: tuple[LifecycleFamily, ...] = ("source", "memory", "topic")
LIFECYCLE_POLICIES: tuple[LifecyclePolicy, ...] = ("all", "any")


@dataclass(frozen=True, slots=True)
class LifecycleNode:
    """One registered dependency node inside one scope."""

    node_id: str
    family: LifecycleFamily
    policy: LifecyclePolicy
    parents: tuple[str, ...]
    reference: str
    entry_id: str | None
    artifact_id: str | None
    revision: int
    ordinal: int
    available: bool


@dataclass(frozen=True, slots=True)
class LifecycleEventRecord:
    """One applied availability event for one registered Source."""

    node_id: str
    revision: int
    sequence: int
    available: bool
    reason: str


class LifecycleRepository:
    """Store lifecycle registrations, availability state, and applied events."""

    async def add_node(self, connection: AsyncConnection, scope_id: str, node: LifecycleNode) -> None:
        await connection.execute(
            insert(SOURCE_LIFECYCLE_NODES_TABLE).values(
                scope_id=scope_id,
                node_id=node.node_id,
                family=node.family,
                policy=node.policy,
                parents=_encode(node.parents),
                reference=node.reference.encode("utf-8"),
                entry_id=node.entry_id,
                artifact_id=node.artifact_id,
                revision=node.revision,
                ordinal=node.ordinal,
                available=node.available,
            )
        )

    async def get_node(
        self,
        connection: AsyncConnection,
        scope_id: str,
        node_id: str,
    ) -> LifecycleNode | None:
        row = (
            await connection.execute(
                select(SOURCE_LIFECYCLE_NODES_TABLE).where(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node_id,
                )
            )
        ).one_or_none()
        return None if row is None else _node(row)

    async def list_nodes(self, connection: AsyncConnection, scope_id: str) -> tuple[LifecycleNode, ...]:
        rows = (
            await connection.execute(
                select(SOURCE_LIFECYCLE_NODES_TABLE)
                .where(SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id)
                .order_by(SOURCE_LIFECYCLE_NODES_TABLE.c.ordinal)
            )
        ).all()
        return tuple(_node(row) for row in rows)

    async def set_availability(
        self,
        connection: AsyncConnection,
        scope_id: str,
        availability: Mapping[str, bool],
    ) -> None:
        for node_id, available in availability.items():
            await connection.execute(
                update(SOURCE_LIFECYCLE_NODES_TABLE)
                .where(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node_id,
                )
                .values(available=available)
            )

    async def record_event(
        self,
        connection: AsyncConnection,
        scope_id: str,
        event: LifecycleEventRecord,
    ) -> None:
        await connection.execute(
            insert(SOURCE_LIFECYCLE_EVENTS_TABLE).values(
                scope_id=scope_id,
                node_id=event.node_id,
                sequence=event.sequence,
                revision=event.revision,
                available=event.available,
                reason=event.reason,
            )
        )

    async def get_event(
        self,
        connection: AsyncConnection,
        scope_id: str,
        node_id: str,
        sequence: int,
    ) -> LifecycleEventRecord | None:
        row = (
            await connection.execute(
                select(SOURCE_LIFECYCLE_EVENTS_TABLE).where(
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.scope_id == scope_id,
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.node_id == node_id,
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.sequence == sequence,
                )
            )
        ).one_or_none()
        return None if row is None else _event(row)

    async def list_events(
        self,
        connection: AsyncConnection,
        scope_id: str,
        node_id: str,
    ) -> tuple[LifecycleEventRecord, ...]:
        rows = (
            await connection.execute(
                select(SOURCE_LIFECYCLE_EVENTS_TABLE)
                .where(
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.scope_id == scope_id,
                    SOURCE_LIFECYCLE_EVENTS_TABLE.c.node_id == node_id,
                )
                .order_by(SOURCE_LIFECYCLE_EVENTS_TABLE.c.sequence)
            )
        ).all()
        return tuple(_event(row) for row in rows)

    async def unavailable_artifact_ids(
        self,
        connection: AsyncConnection,
        scope_id: str,
        family: LifecycleFamily,
    ) -> frozenset[str]:
        rows = (
            await connection.execute(
                select(SOURCE_LIFECYCLE_NODES_TABLE.c.artifact_id).where(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.family == family,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.available.is_(False),
                    SOURCE_LIFECYCLE_NODES_TABLE.c.artifact_id.is_not(None),
                )
            )
        ).all()
        return frozenset(str(row.artifact_id) for row in rows)


def _encode(value: tuple[str, ...]) -> bytes:
    return json.dumps(list(value), ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _decode(value: object) -> tuple[str, ...]:
    if not isinstance(value, bytes | bytearray | str):
        raise InvalidStoredColumnError("parents", "text")
    text = value.decode("utf-8") if isinstance(value, bytes | bytearray) else value
    decoded = json.loads(text)
    if not isinstance(decoded, list) or not all(isinstance(item, str) for item in decoded):
        raise InvalidStoredColumnError("parents", "text")
    return tuple(decoded)


def _node(row: object) -> LifecycleNode:
    return LifecycleNode(
        node_id=str(row.node_id),
        family=str(row.family),
        policy=str(row.policy),
        parents=_decode(row.parents),
        reference=row.reference.decode("utf-8") if isinstance(row.reference, bytes | bytearray) else str(row.reference),
        entry_id=None if row.entry_id is None else str(row.entry_id),
        artifact_id=None if row.artifact_id is None else str(row.artifact_id),
        revision=int(row.revision),
        ordinal=int(row.ordinal),
        available=bool(row.available),
    )


def _event(row: object) -> LifecycleEventRecord:
    return LifecycleEventRecord(
        node_id=str(row.node_id),
        revision=int(row.revision),
        sequence=int(row.sequence),
        available=bool(row.available),
        reason=str(row.reason),
    )


__all__ = [
    "LIFECYCLE_FAMILIES",
    "LIFECYCLE_POLICIES",
    "LifecycleEventRecord",
    "LifecycleFamily",
    "LifecycleNode",
    "LifecyclePolicy",
    "LifecycleRepository",
]
