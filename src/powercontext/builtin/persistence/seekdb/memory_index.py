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

"""One-statement hybrid candidate retrieval over the embedded seekdb dialect."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pyobvector import VECTOR
from sqlalchemy import TextClause, bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.memory import (
    EmbeddingProfile,
    MemorySearchChannels,
    MemorySearchRequest,
)
from powercontext.builtin.artifacts.memory.canonical import validate_embedding
from powercontext.builtin.persistence.memory_index import memory_channel_hits
from powercontext.builtin.persistence.tags import memory_tag_parameters, memory_tag_sql
from powercontext.builtin.tags import TagFilter

_FTS_CHANNEL = 1
_VECTOR_CHANNEL = 2

# One statement carries both candidate channels. The full-text branch reads the
# frozen head projection through its native full-text index, the vector branch
# reads the frozen embedding projection through its native vector index, and the
# outer ordering restores each branch's own ranking so the shared fusion sees the
# same channel order the two separate statements used to produce.
_HYBRID_SEARCH_SQL = """
SELECT c.memory_artifact_id AS memory_artifact_id,
       c.head_revision AS head_revision,
       c.entry_id AS entry_id,
       c.entry_version_id AS entry_version_id,
       c.text AS text,
       c.distance AS distance,
       c.channel AS channel
FROM (
    (SELECT h.memory_artifact_id AS memory_artifact_id,
            h.head_revision AS head_revision,
            h.entry_id AS entry_id,
            h.entry_version_id AS entry_version_id,
            v.text AS text,
            NULL AS distance,
            1 AS channel,
            MATCH(h.searchable_text) AGAINST (:analyzed_query) AS score
     FROM pc_memory_entry_heads AS h
     JOIN pc_memory_entry_versions AS v
       ON v.scope_id = h.scope_id
      AND v.memory_artifact_id = h.memory_artifact_id
      AND v.entry_version_id = h.entry_version_id
     WHERE h.scope_id = :scope_id
       AND h.memory_artifact_id IN :memory_artifact_ids
       AND MATCH(h.searchable_text) AGAINST (:analyzed_query)
       /* fts-tag-filter */
     ORDER BY MATCH(h.searchable_text) AGAINST (:analyzed_query) DESC,
              h.memory_artifact_id, h.entry_id, h.entry_version_id
     LIMIT :candidate_limit)
    UNION ALL
    (SELECT m.memory_artifact_id AS memory_artifact_id,
            m.head_revision AS head_revision,
            m.entry_id AS entry_id,
            m.entry_version_id AS entry_version_id,
            v.text AS text,
            l2_distance(m.embedding, :query_vector) AS distance,
            2 AS channel,
            NULL AS score
     FROM pc_memory_vector_entries AS m
     JOIN pc_memory_entry_versions AS v
       ON v.scope_id = m.scope_id
      AND v.memory_artifact_id = m.memory_artifact_id
      AND v.entry_version_id = m.entry_version_id
     WHERE m.scope_id = :scope_id
       AND m.memory_artifact_id IN :memory_artifact_ids
       /* vector-tag-filter */
     ORDER BY l2_distance(m.embedding, :query_vector) APPROXIMATE
     LIMIT :candidate_limit)
) AS c
ORDER BY c.channel,
         CASE WHEN c.channel = 2 THEN c.distance END,
         CASE WHEN c.channel = 1 THEN c.score END DESC,
         c.memory_artifact_id, c.entry_id, c.entry_version_id
"""

# A tag filter cannot be pushed into the approximate vector scan: the tagged
# branch evaluates the exact distance over the eligible set, so a tagged entry
# is never dropped from the vector channel. That mirrors the separate tagged
# vector statement the frozen implementation used. The untagged branch keeps
# APPROXIMATE, so its plan uses the native vector index.
_TAGGED_HYBRID_SEARCH_SQL = (
    _HYBRID_SEARCH_SQL
    .replace("/* fts-tag-filter */", memory_tag_sql("h"))
    .replace("/* vector-tag-filter */", memory_tag_sql("m"))
    .replace(" APPROXIMATE", "")
)


async def search_hybrid_candidates(
    connection: AsyncConnection,
    scope_id: str,
    request: MemorySearchRequest,
    /,
    *,
    profile: EmbeddingProfile,
) -> MemorySearchChannels:
    """Return both candidate channels from a single statement."""

    statement = _hybrid_statement(request.tag_filter, profile)
    parameters: dict[str, Any] = {
        "scope_id": scope_id,
        "memory_artifact_ids": tuple(memory.artifact_id for memory in request.memories),
        "analyzed_query": request.analyzed_query,
        "query_vector": validate_embedding(request.query_vector, dimension=profile.dimension),
        "candidate_limit": request.candidate_limit,
        **memory_tag_parameters(request.tag_filter),
    }
    rows = (await connection.execute(statement, parameters)).mappings()
    fts: list[Mapping[Any, Any]] = []
    vector: list[Mapping[Any, Any]] = []
    for row in rows:
        if int(row["channel"]) == _FTS_CHANNEL:
            fts.append(row)
        elif int(row["channel"]) == _VECTOR_CHANNEL:
            vector.append(row)
    return MemorySearchChannels(
        fts=memory_channel_hits(fts, request.memories),
        vector=memory_channel_hits(vector, request.memories),
    )


def _hybrid_statement(
    tag_filter: TagFilter | None,
    profile: EmbeddingProfile,
) -> TextClause:
    """Bind the one-statement hybrid query for the requested filter shape."""

    sql = _HYBRID_SEARCH_SQL if tag_filter is None else _TAGGED_HYBRID_SEARCH_SQL
    statement = text(sql).bindparams(
        bindparam("memory_artifact_ids", expanding=True),
        bindparam("query_vector", type_=VECTOR(profile.dimension)),
    )
    if tag_filter is None:
        return statement
    return statement.bindparams(
        bindparam("tag_keys", expanding=True),
        bindparam("tag_hashes", expanding=True),
    )
