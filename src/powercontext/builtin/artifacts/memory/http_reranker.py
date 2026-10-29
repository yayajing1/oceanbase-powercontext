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

"""HTTP scoring service adapter for listwise Memory reranking."""

from __future__ import annotations

import math

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage

HTTP_RERANKER_POLICY_ID = "http-cross-encoder-v1"

_RESPONSE_FIELDS = frozenset({"scores"})
_SCORE_FIELDS = frozenset({"rank", "score"})


class HTTPMemoryReranker:
    """Score Memory candidates with an external HTTP service in bounded batches.

    The service receives the original one-based rank of every candidate, so a
    batch never renumbers the candidate pool. Scores from all batches take part
    in one global selection: descending score, then ascending original rank.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = HTTP_RERANKER_POLICY_ID,
    ) -> None:
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError("rerank batch size must be a positive integer")  # noqa: TRY003
        self._endpoint = endpoint
        self._client = client
        self._batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Score every candidate and select the globally best original ranks."""

        _validate_bounds(len(candidates), limit)
        scores: dict[int, float] = {}
        requests = 0
        for start in range(0, len(candidates), self._batch_size):
            batch = candidates[start : start + self._batch_size]
            ranks = tuple(range(start + 1, start + len(batch) + 1))
            payload = {
                "query": query,
                "candidates": [
                    {"rank": rank, "text": candidate.text} for rank, candidate in zip(ranks, batch, strict=True)
                ],
            }
            response = await self._client.post(self._endpoint, json=payload)
            _raise_for_status(response)
            requests += 1
            scores.update(_parse_scores(response, ranks))
        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        selected = tuple(rank for rank, _ in ordered[:limit])
        return MemoryRerankDecision(
            selected_ranks=selected,
            usage=InferenceUsage(requests=requests),
            discarded_rank_count=len(candidates) - len(selected),
        )


def _raise_for_status(response: httpx.Response) -> None:
    """Keep every non-2xx response as an httpx exception."""

    response.raise_for_status()
    if not 200 <= response.status_code < 300:
        raise httpx.HTTPStatusError(  # noqa: TRY003
            f"reranker service returned HTTP {response.status_code}",
            request=response.request,
            response=response,
        )


def _decode_score_records(response: httpx.Response) -> list:
    """Validate the response envelope and return its score records."""

    try:
        payload = response.json()
    except ValueError as error:
        raise ValueError("reranker response must be valid JSON") from error  # noqa: TRY003
    if not isinstance(payload, dict) or set(payload) != _RESPONSE_FIELDS:
        raise ValueError("reranker response must contain exactly one 'scores' field")  # noqa: TRY003
    records = payload["scores"]
    if not isinstance(records, list):
        raise ValueError("reranker response 'scores' must be a list")  # noqa: TRY003, TRY004
    return records


def _parse_score(record: object) -> tuple[int, float]:
    """Validate one score record and return its original rank and finite score."""

    if not isinstance(record, dict) or set(record) != _SCORE_FIELDS:
        raise ValueError("reranker score records must contain exactly 'rank' and 'score'")  # noqa: TRY003
    rank = record["rank"]
    if not isinstance(rank, int) or isinstance(rank, bool):
        raise ValueError("reranker score rank must be an integer")  # noqa: TRY003, TRY004
    score = record["score"]
    if not isinstance(score, (int, float)) or isinstance(score, bool) or not math.isfinite(score):
        raise ValueError("reranker score must be a finite number")  # noqa: TRY003
    return rank, float(score)


def _parse_scores(response: httpx.Response, ranks: tuple[int, ...]) -> dict[int, float]:
    """Validate one batch response and map its scores back to the original ranks."""

    expected = set(ranks)
    parsed: dict[int, float] = {}
    for record in _decode_score_records(response):
        rank, score = _parse_score(record)
        if rank not in expected:
            raise ValueError("reranker score rank does not belong to the requested batch")  # noqa: TRY003
        if rank in parsed:
            raise ValueError("reranker returned a duplicated rank")  # noqa: TRY003
        parsed[rank] = score
    if set(parsed) != expected:
        raise ValueError("reranker response must cover every requested rank exactly once")  # noqa: TRY003
    return parsed


def _validate_bounds(candidate_count: int, limit: int) -> None:
    """Reject a rerank request that cannot produce a meaningful selection."""

    if candidate_count < 1:
        raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
    if limit < 1 or limit > candidate_count:
        raise ValueError("rerank limit must be between one and the candidate count")  # noqa: TRY003


__all__ = ["HTTP_RERANKER_POLICY_ID", "HTTPMemoryReranker"]
