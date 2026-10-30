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

"""Coalesce concurrent embedding requests and cache successful vectors."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import EmbeddingProfile
    from powercontext.builtin.inference.protocols import EmbeddingModel

__all__ = ["CoalescingEmbeddingModel"]

OutcomeT = TypeVar("OutcomeT")


@dataclass(frozen=True, slots=True)
class _Outcome:
    """One resolved vector together with the usage of the batch that produced it."""

    batch_id: int
    vector: EmbeddingVector
    usage: InferenceUsage


def _discard_exception(future: asyncio.Future[OutcomeT]) -> None:
    """Retrieve a batch failure that no waiter observed, keeping asyncio quiet."""

    if not future.cancelled():
        future.exception()


class CoalescingEmbeddingModel:
    """Share pending embedding work between callers and cache successful vectors.

    One call deduplicates its own texts and joins the pending or running batch
    that already covers a text, so concurrent callers and late arrivals reuse
    the same provider request instead of repeating it. Every provider request
    carries at most ``max_batch_size`` deduplicated texts, and texts registered
    within ``coalesce_ms`` are dispatched together.

    Successful vectors are kept in a bounded LRU cache so later calls reuse them
    without a provider request. A failed batch is never cached and stays
    retryable, and cancelling one caller never cancels the shared provider work
    other callers wait for.
    """

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if max_batch_size < 1:
            raise ValueError("max_batch_size must be at least 1")  # noqa: TRY003
        if coalesce_ms < 0:
            raise ValueError("coalesce_ms must not be negative")  # noqa: TRY003
        if cache_size < 0:
            raise ValueError("cache_size must not be negative")  # noqa: TRY003
        self._model = model
        self._max_batch_size = max_batch_size
        self._coalesce_ms = coalesce_ms
        self._cache_size = cache_size
        self.profile: EmbeddingProfile = model.profile
        self._cache: OrderedDict[str, EmbeddingVector] = OrderedDict()
        self._pending: OrderedDict[str, None] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[_Outcome]] = {}
        self._batches: set[asyncio.Task[None]] = set()
        self._dispatch_timer: asyncio.TimerHandle | None = None
        self._batch_counter = 0
        self._loop: asyncio.AbstractEventLoop | None = None

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Return one vector per input text, preserving order and duplicates."""

        ordered = tuple(texts)
        if not ordered:
            return EmbeddingResult(vectors=(), usage=InferenceUsage(requests=0))
        loop = self._bind_loop()
        vectors: dict[str, EmbeddingVector] = {}
        needed: list[tuple[str, asyncio.Future[_Outcome]]] = []
        for text in dict.fromkeys(ordered):
            cached = self._cached_vector(text)
            if cached is not None:
                vectors[text] = cached
                continue
            needed.append((text, self._register(text, loop)))
        outcomes = await self._wait(needed)
        for text, outcome in zip((text for text, _ in needed), outcomes, strict=True):
            vectors[text] = outcome.vector
        return EmbeddingResult(
            vectors=tuple(vectors[text] for text in ordered),
            usage=self._summarize(outcomes),
        )

    def _bind_loop(self) -> asyncio.AbstractEventLoop:
        """Bind the wrapper to the running loop and drop work of a closed loop."""

        loop = asyncio.get_running_loop()
        if self._loop is loop:
            return loop
        if self._loop is not None:
            self._inflight.clear()
            self._pending.clear()
            if self._dispatch_timer is not None:
                self._dispatch_timer.cancel()
                self._dispatch_timer = None
        self._loop = loop
        return loop

    def _register(self, text: str, loop: asyncio.AbstractEventLoop) -> asyncio.Future[_Outcome]:
        """Return the shared future covering one text, queueing new work."""

        existing = self._inflight.get(text)
        if existing is not None:
            return existing
        future: asyncio.Future[_Outcome] = loop.create_future()
        future.add_done_callback(_discard_exception)
        self._inflight[text] = future
        self._pending[text] = None
        if self._dispatch_timer is None:
            self._dispatch_timer = loop.call_later(self._coalesce_ms / 1000, self._dispatch_pending)
        return future

    async def _wait(self, needed: list[tuple[str, asyncio.Future[_Outcome]]]) -> list[_Outcome]:
        """Await the shared batches of one call without cancelling them for others."""

        if not needed:
            return []
        gathered = asyncio.gather(*(future for _, future in needed))
        gathered.add_done_callback(_discard_exception)
        return list(await asyncio.shield(gathered))

    def _dispatch_pending(self) -> None:
        """Send every queued text to the provider in bounded batches."""

        self._dispatch_timer = None
        batch: list[str] = []
        while self._pending:
            text, _ = self._pending.popitem(last=False)
            batch.append(text)
            if len(batch) >= self._max_batch_size:
                self._start_batch(tuple(batch))
                batch = []
        if batch:
            self._start_batch(tuple(batch))

    def _start_batch(self, texts: tuple[str, ...]) -> None:
        """Run one provider request in its own task so waiters stay independent."""

        batch_id = self._batch_counter
        self._batch_counter += 1
        task = asyncio.create_task(self._run_batch(batch_id, texts))
        self._batches.add(task)
        task.add_done_callback(self._batches.discard)

    async def _run_batch(self, batch_id: int, texts: tuple[str, ...]) -> None:
        """Resolve or fail every waiter of one provider request."""

        try:
            result = await self._model.embed(texts)
        except asyncio.CancelledError:
            self._fail_batch(texts, asyncio.CancelledError("embedding batch was cancelled"))
            raise
        except Exception as error:
            self._fail_batch(texts, error)
            return
        if len(result.vectors) != len(texts):
            self._fail_batch(texts, ValueError("embedding provider returned a mismatched vector count"))
            return
        self._complete_batch(batch_id, texts, result.vectors, result.usage)

    def _complete_batch(
        self,
        batch_id: int,
        texts: tuple[str, ...],
        vectors: tuple[EmbeddingVector, ...],
        usage: InferenceUsage,
    ) -> None:
        """Publish one successful batch to its waiters and cache its vectors."""

        for text, vector in zip(texts, vectors, strict=True):
            self._store(text, vector)
            future = self._inflight.pop(text, None)
            if future is not None and not future.done():
                future.set_result(_Outcome(batch_id=batch_id, vector=vector, usage=usage))

    def _fail_batch(self, texts: tuple[str, ...], error: BaseException) -> None:
        """Publish one failed batch to its waiters and keep the texts retryable."""

        for text in texts:
            future = self._inflight.pop(text, None)
            if future is not None and not future.done():
                future.set_exception(error)

    def _cached_vector(self, text: str) -> EmbeddingVector | None:
        """Return a cached vector and refresh its recency."""

        if self._cache_size < 1:
            return None
        vector = self._cache.get(text)
        if vector is None:
            return None
        self._cache.move_to_end(text)
        return vector

    def _store(self, text: str, vector: EmbeddingVector) -> None:
        """Cache one successful vector within the bounded LRU capacity."""

        if self._cache_size < 1:
            return
        self._cache[text] = vector
        self._cache.move_to_end(text)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def _summarize(self, outcomes: list[_Outcome]) -> InferenceUsage:
        """Charge every distinct batch one caller waited for exactly once."""

        requests = 0
        input_tokens = 0
        output_tokens = 0
        counted: set[int] = set()
        has_input = False
        has_output = False
        for outcome in outcomes:
            if outcome.batch_id in counted:
                continue
            counted.add(outcome.batch_id)
            requests += outcome.usage.requests
            if outcome.usage.input_tokens is not None:
                has_input = True
                input_tokens += outcome.usage.input_tokens
            if outcome.usage.output_tokens is not None:
                has_output = True
                output_tokens += outcome.usage.output_tokens
        return InferenceUsage(
            requests=requests,
            input_tokens=input_tokens if has_input else None,
            output_tokens=output_tokens if has_output else None,
        )
