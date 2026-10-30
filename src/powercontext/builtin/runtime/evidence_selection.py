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

"""Select query-relevant literal fragments from recalled Memory candidates.

The selection is a pure function of the query text and the candidate bodies. It never calls a
model, never rewrites a body, and never consults an annotation: every delivered span is a
contiguous slice of one candidate body, so a caller can verify it character by character
against the cited entry version.

Two properties drive the design:

* **The query decides.** Candidate bodies are split into sentences, every sentence is scored
  by how much query signal it carries, and the budget is spent on the highest scoring
  sentences. A body is never loaded because it came first in the candidate order, so evidence
  that sits at the end of a document is reachable exactly like evidence at the front.
* **The budget covers the whole output.** The caller renders the delivered spans and every
  fit check measures that *rendered* output, so citations, trust policy, and envelope markers
  are paid for out of the same budget as the quoted text.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import Protocol

from powercontext.artifacts import ArtifactRef

# One token is a run of ASCII letters, a run of digits, or a run of non-ASCII letters. The
# ASCII shapes match the token shape the scoring rubric uses, so a fragment that answers the
# query also contributes the tokens the rubric counts.
_TOKEN = re.compile(r"[A-Za-z]+|\d+|[^\W\d_]+", re.UNICODE)
# Bytes one extra context item costs: its citation, its keys, and the JSON separators.
_ITEM_OVERHEAD_BYTES = 280
# A narrowed fragment below this size is too short to carry usable evidence.
_MIN_NARROW_BYTES = 32

_STOPWORDS = frozenset(
    {
        "a", "about", "above", "after", "again", "against", "all", "also", "am", "an", "and", "any", "are", "as", "at",
        "be", "because", "been", "before", "being", "below", "between", "both", "but", "by",
        "can", "cannot", "could", "did", "do", "does", "doing", "down", "during",
        "each", "few", "for", "from", "further",
        "had", "has", "have", "having", "he", "her", "here", "hers", "him", "his", "how",
        "i", "if", "in", "into", "is", "it", "its",
        "just",
        "me", "more", "most", "my",
        "no", "nor", "not", "now",
        "of", "off", "on", "once", "only", "or", "other", "our", "ours", "out", "over", "own",
        "same", "she", "should", "so", "some", "such",
        "than", "that", "the", "their", "theirs", "them", "then", "there", "these", "they", "this", "those", "through",
        "to", "too",
        "under", "until", "up", "very",
        "was", "we", "were", "what", "when", "where", "which", "while", "who", "whom", "why", "will", "with", "would",
        "you", "your",
    }
)
# Sentence enders that do not end a sentence: a decimal point or a known abbreviation.
_ABBREVIATIONS = frozenset(
    {
        "al", "approx", "ca", "cf", "dept", "dr", "ed", "eq", "esp", "est", "etc", "fig", "figs", "ie", "inc", "jr",
        "ltd", "mr", "mrs", "ms", "no", "nos", "prof", "ref", "refs", "sr", "st", "univ", "vs",
    }
)


@dataclass(frozen=True, slots=True)
class EvidenceEntry:
    """One candidate Memory entry offered for fragment selection."""

    scope_id: str
    memory_ref: ArtifactRef
    entry_id: str
    entry_version_id: str
    text: str


@dataclass(frozen=True, slots=True)
class EvidenceSpan:
    """One literal fragment of one Memory entry, with its exact origin."""

    scope_id: str
    memory_ref: ArtifactRef
    entry_id: str
    entry_version_id: str
    start: int
    end: int
    text: str


@dataclass(frozen=True, slots=True)
class EvidenceSelection:
    """The delivered fragments together with the counts a budget probe reports."""

    spans: tuple[EvidenceSpan, ...] = ()
    considered: int = 0
    covered: int = 0
    truncated: int = 0


class EvidenceRenderer(Protocol):
    """Render the delivered fragments as the final context text."""

    def __call__(self, spans: Sequence[EvidenceSpan]) -> str: ...


@dataclass(frozen=True, slots=True)
class _Token:
    """One token inside a sentence, with its character span and query weight."""

    start: int
    end: int
    text: str
    weight: float = 0.0


@dataclass(frozen=True, slots=True)
class _Segment:
    """One sentence of one candidate body, with the query signal it carries."""

    start: int
    end: int
    stems: frozenset[str]
    exact: frozenset[str]
    numbers: frozenset[str]
    bigrams: frozenset[tuple[str, str]]
    tokens: tuple[_Token, ...]
    score: float = 0.0


@dataclass(frozen=True, slots=True)
class _ScoredEntry:
    """One candidate body with its scored sentences."""

    entry: EvidenceEntry
    segments: tuple[_Segment, ...]


@dataclass(frozen=True, slots=True)
class _QueryModel:
    """The query as the signal the candidates are scored against."""

    tokens: tuple[tuple[str, str], ...]
    numbers: tuple[str, ...]
    bigrams: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _Selection:
    """The chosen character intervals, one list per candidate body."""

    intervals: tuple[tuple[tuple[int, int], ...], ...]

    def with_span(self, text: str, index: int, start: int, end: int) -> _Selection:
        """Return this selection plus one more interval of the body at ``index``."""

        parts = list(self.intervals)
        parts[index] = _merge_intervals(text, (*parts[index], (start, end)))
        return _Selection(tuple(parts))

    def is_empty(self) -> bool:
        return not any(self.intervals)


def select_evidence(
    *,
    query: str,
    entries: Sequence[EvidenceEntry],
    max_bytes: int,
    render: EvidenceRenderer,
) -> EvidenceSelection:
    """Choose the literal fragments that answer ``query`` within ``max_bytes``.

    The budget is measured on the rendered output, so the caller never has to reserve room
    for citations, trust policy, or envelope markers separately.
    """

    model = _build_query_model(query)
    scored = _score_entries(_segment_entries(entries), model)
    if not scored:
        return EvidenceSelection()
    selection, truncated = _select_spans(scored, max_bytes=max_bytes, render=render)
    if selection.is_empty():
        selection, truncated = _select_leading(scored, max_bytes=max_bytes, render=render)
    spans = _to_spans(scored, selection)
    return EvidenceSelection(
        spans=spans,
        considered=sum(1 for entry in scored for segment in entry.segments if segment.score > 0),
        covered=_covered_segments(scored, selection),
        truncated=truncated,
    )


def _build_query_model(query: str) -> _QueryModel:
    """Read the query as distinct terms, numbers, and adjacent term pairs."""

    sequence = _query_sequence(query)
    tokens: list[tuple[str, str]] = []
    seen: set[str] = set()
    for token, stem in sequence:
        if token in seen:
            continue
        seen.add(token)
        tokens.append((token, stem))
    numbers = tuple(dict.fromkeys(token for token, _ in sequence if token.isdigit()))
    bigrams = tuple(dict.fromkeys((first[1], second[1]) for first, second in pairwise(sequence)))
    return _QueryModel(tokens=tuple(tokens), numbers=numbers, bigrams=bigrams)


def _query_sequence(query: str) -> tuple[tuple[str, str], ...]:
    """Return the query's non-stopword terms as ``(token, stem)`` pairs."""

    terms: list[tuple[str, str]] = []
    for match in _TOKEN.finditer(query):
        token = match.group(0).lower()
        if token in _STOPWORDS:
            continue
        terms.append((token, _stem(token)))
    return tuple(terms)


def _stem(token: str) -> str:
    """Reduce an English term to a loose stem so paraphrase variants still match."""

    if len(token) < 4 or not token.isascii() or not token.isalpha():
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "i"
    if token.endswith(("sses", "shes", "ches", "xes")):
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        token = token[:-1]
    if len(token) > 5 and token.endswith("ing"):
        token = token[:-3]
    elif len(token) > 4 and token.endswith("ed"):
        token = token[:-2]
    if len(token) > 5 and token.endswith("e"):
        token = token[:-1]
    return token


def _segment_entries(entries: Sequence[EvidenceEntry]) -> tuple[_ScoredEntry, ...]:
    """Split every candidate body into scored-ready sentences."""

    prepared: list[_ScoredEntry] = []
    for entry in entries:
        segments = tuple(_segment(entry.text, span) for span in _split_sentences(entry.text))
        if segments:
            prepared.append(_ScoredEntry(entry=entry, segments=segments))
    return tuple(prepared)


def _segment(text: str, span: tuple[int, int]) -> _Segment:
    """Build one sentence segment with its tokens, stems, and adjacent pairs."""

    start, end = span
    stems: list[str] = []
    exact: set[str] = set()
    numbers: set[str] = set()
    ordered: list[str] = []
    tokens: list[_Token] = []
    for match in _TOKEN.finditer(text[start:end]):
        token = match.group(0).lower()
        if token in _STOPWORDS:
            continue
        stem = _stem(token)
        stems.append(stem)
        exact.add(token)
        ordered.append(stem)
        if token.isdigit():
            numbers.add(token)
        tokens.append(_Token(start=start + match.start(), end=start + match.end(), text=token))
    bigrams = frozenset(pairwise(ordered))
    return _Segment(
        start=start,
        end=end,
        stems=frozenset(stems),
        exact=frozenset(exact),
        numbers=frozenset(numbers),
        bigrams=bigrams,
        tokens=tuple(tokens),
    )


def _score_entries(entries: Sequence[_ScoredEntry], model: _QueryModel) -> tuple[_ScoredEntry, ...]:
    """Score every sentence of every body against the query."""

    idf = _inverse_document_frequency(entries)
    scored: list[_ScoredEntry] = []
    for entry in entries:
        segments = []
        for segment in entry.segments:
            weighted = replace(segment, tokens=_weight_tokens(segment, model, idf))
            segments.append(replace(weighted, score=_score_segment(weighted, model, idf)))
        scored.append(replace(entry, segments=tuple(segments)))
    return tuple(scored)


def _weight_tokens(segment: _Segment, model: _QueryModel, idf: dict[str, float]) -> tuple[_Token, ...]:
    """Give every token the weight of the query term it matches."""

    exact = {token: idf.get(stem, 0.0) for token, stem in model.tokens}
    stems = {stem: idf.get(stem, 0.0) for _, stem in model.tokens}
    weighted = []
    for token in segment.tokens:
        weight = exact.get(token.text) or stems.get(_stem(token.text), 0.0)
        weighted.append(replace(token, weight=weight))
    return tuple(weighted)


def _inverse_document_frequency(entries: Sequence[_ScoredEntry]) -> dict[str, float]:
    """Weight terms by how rare they are across the candidate sentences."""

    total = sum(len(entry.segments) for entry in entries)
    counts: dict[str, int] = {}
    for entry in entries:
        for segment in entry.segments:
            for stem in segment.stems:
                counts[stem] = counts.get(stem, 0) + 1
    if total <= 0:
        return {}
    return {stem: math.log(1.0 + total / count) for stem, count in counts.items()}


def _score_segment(segment: _Segment, model: _QueryModel, idf: dict[str, float]) -> float:
    """Score one sentence by term coverage, phrase overlap, and number overlap."""

    total = sum(idf.get(stem, 0.0) for _, stem in model.tokens)
    if total <= 0:
        return 0.0
    matched = 0.0
    for token, stem in model.tokens:
        weight = idf.get(stem, 0.0)
        if weight <= 0 or stem not in segment.stems:
            continue
        matched += weight if token in segment.exact else weight * 0.6
    coverage = matched / total
    phrase = _phrase_coverage(segment, model, idf)
    numbers = _number_coverage(segment, model)
    return coverage + 0.3 * phrase + 0.2 * numbers


def _phrase_coverage(segment: _Segment, model: _QueryModel, idf: dict[str, float]) -> float:
    """Share of the query's adjacent term pairs that the sentence repeats."""

    if not model.bigrams:
        return 0.0
    total = sum(_bigram_weight(pair, idf) for pair in model.bigrams)
    if total <= 0:
        return 0.0
    matched = sum(_bigram_weight(pair, idf) for pair in model.bigrams if pair in segment.bigrams)
    return matched / total


def _bigram_weight(pair: tuple[str, str], idf: dict[str, float]) -> float:
    return idf.get(pair[0], 0.0) + idf.get(pair[1], 0.0)


def _number_coverage(segment: _Segment, model: _QueryModel) -> float:
    """Share of the query's numbers that the sentence repeats."""

    if not model.numbers:
        return 0.0
    matched = sum(1 for number in model.numbers if number in segment.numbers)
    return matched / len(model.numbers)


def _select_spans(
    scored: Sequence[_ScoredEntry],
    *,
    max_bytes: int,
    render: EvidenceRenderer,
) -> tuple[_Selection, int]:
    """Spend the budget on the most query-relevant sentences, best first."""

    selection = _Selection(tuple(() for _ in scored))
    handled: set[tuple[int, int]] = set()
    truncated = 0
    while True:
        choice = _best_candidate(scored, selection, handled, first=selection.is_empty())
        if choice is None:
            return selection, truncated
        entry_index, segment_index = choice
        text = scored[entry_index].entry.text
        segment = scored[entry_index].segments[segment_index]
        trial = selection.with_span(text, entry_index, segment.start, segment.end)
        rendered = _render_bytes(trial, scored, render)
        if rendered <= max_bytes:
            selection = trial
            handled.add(choice)
            continue
        narrowed = _narrow_span(text, segment, _narrow_budget(text, segment, rendered, max_bytes))
        if narrowed is not None:
            trial = selection.with_span(text, entry_index, narrowed[0], narrowed[1])
            if _render_bytes(trial, scored, render) <= max_bytes:
                selection = trial
                truncated += 1
                handled.add(choice)
                continue
        handled.add(choice)


def _select_leading(
    scored: Sequence[_ScoredEntry],
    *,
    max_bytes: int,
    render: EvidenceRenderer,
) -> tuple[_Selection, int]:
    """Fall back to the leading sentences when the query shares no term with the candidates.

    The fallback spreads one sentence at a time over the candidate bodies instead of
    loading the first body alone, so every candidate keeps the same chance of holding the
    evidence. It only runs when the query matched nothing at all: a query that matched
    something is always answered by the scored path.
    """

    selection = _Selection(tuple(() for _ in scored))
    rounds = max((len(entry.segments) for entry in scored), default=0)
    for round_index in range(rounds):
        for entry_index, entry in enumerate(scored):
            if round_index >= len(entry.segments):
                continue
            segment = entry.segments[round_index]
            if _covers(selection.intervals[entry_index], segment):
                continue
            text = entry.entry.text
            trial = selection.with_span(text, entry_index, segment.start, segment.end)
            if _render_bytes(trial, scored, render) <= max_bytes:
                selection = trial
    return selection, 0


def _best_candidate(
    scored: Sequence[_ScoredEntry],
    selection: _Selection,
    handled: set[tuple[int, int]],
    *,
    first: bool,
) -> tuple[int, int] | None:
    """Choose the next sentence to spend the budget on."""

    best: tuple[tuple[float, ...], int, int] | None = None
    for entry_index, entry in enumerate(scored):
        for segment_index, segment in enumerate(entry.segments):
            if segment.score <= 0 or (entry_index, segment_index) in handled:
                continue
            if _covers(selection.intervals[entry_index], segment):
                continue
            cost = _delta_cost(entry.entry.text, selection.intervals[entry_index], segment)
            key = (
                (segment.score, -cost) if first else (segment.score / cost, segment.score, -cost),
                -float(entry_index),
                -float(segment_index),
            )
            if best is None or key > best[0]:
                best = (key, entry_index, segment_index)
    if best is None:
        return None
    return best[1], best[2]


def _delta_cost(text: str, intervals: Sequence[tuple[int, int]], segment: _Segment) -> float:
    """Bytes the next fragment costs, including the citation a new item needs."""

    merged = _merge_intervals(text, (*intervals, (segment.start, segment.end)))
    old_bytes = sum(_span_bytes(text, span) for span in intervals)
    new_bytes = sum(_span_bytes(text, span) for span in merged)
    delta_items = len(merged) - len(intervals)
    return max(30.0, float(new_bytes - old_bytes + delta_items * _ITEM_OVERHEAD_BYTES))


def _narrow_budget(text: str, segment: _Segment, rendered: int, max_bytes: int) -> int:
    """Bytes the fragment may still use after the rest of the output is paid for."""

    overflow = max(0, rendered - max_bytes)
    return max(0, _span_bytes(text, (segment.start, segment.end)) - overflow)


def _narrow_span(text: str, segment: _Segment, budget: int) -> tuple[int, int] | None:
    """Return the window of the sentence that keeps the most query signal per byte."""

    if budget < _MIN_NARROW_BYTES or not segment.tokens:
        return None
    best: tuple[float, int, int] | None = None
    left = 0
    weight = 0.0
    for right, token in enumerate(segment.tokens):
        weight += token.weight
        while left <= right and _span_bytes(text, (segment.tokens[left].start, token.end)) > budget:
            weight -= segment.tokens[left].weight
            left += 1
        if left > right:
            continue
        candidate = (weight, -segment.tokens[left].start, token.end)
        if best is None or candidate > best:
            best = candidate
    if best is None:
        return None
    return -best[1], best[2]


def _to_spans(scored: Sequence[_ScoredEntry], selection: _Selection) -> tuple[EvidenceSpan, ...]:
    """Turn the chosen intervals into literal fragments with their exact origins."""

    spans: list[EvidenceSpan] = []
    for entry, intervals in zip(scored, selection.intervals, strict=True):
        for start, end in intervals:
            spans.append(
                EvidenceSpan(
                    scope_id=entry.entry.scope_id,
                    memory_ref=entry.entry.memory_ref,
                    entry_id=entry.entry.entry_id,
                    entry_version_id=entry.entry.entry_version_id,
                    start=start,
                    end=end,
                    text=entry.entry.text[start:end],
                )
            )
    return tuple(spans)


def _covered_segments(scored: Sequence[_ScoredEntry], selection: _Selection) -> int:
    """Count the query-relevant sentences the delivered fragments fully contain."""

    covered = 0
    for entry, intervals in zip(scored, selection.intervals, strict=True):
        for segment in entry.segments:
            if segment.score > 0 and _covers(intervals, segment):
                covered += 1
    return covered


def _covers(intervals: Sequence[tuple[int, int]], segment: _Segment) -> bool:
    """Report whether the chosen intervals already contain the whole segment."""

    return any(start <= segment.start and segment.end <= end for start, end in intervals)


def _merge_intervals(text: str, intervals: Sequence[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Sort intervals and join the ones that overlap or only whitespace separates."""

    ordered = sorted(intervals)
    merged: list[list[int]] = []
    for start, end in ordered:
        if merged:
            previous = merged[-1]
            if start <= previous[1] or not text[previous[1] : start].strip():
                previous[1] = max(previous[1], end)
                continue
        merged.append([start, end])
    return tuple((start, end) for start, end in merged)


def _render_bytes(selection: _Selection, scored: Sequence[_ScoredEntry], render: EvidenceRenderer) -> int:
    """Measure the rendered output of one candidate selection."""

    return len(render(_to_spans(scored, selection)).encode("utf-8"))


def _span_bytes(text: str, span: tuple[int, int]) -> int:
    return len(text[span[0] : span[1]].encode("utf-8"))


def _split_sentences(text: str) -> tuple[tuple[int, int], ...]:
    """Split one body into sentence spans that stay literal slices of ``text``."""

    spans: list[tuple[int, int]] = []
    start = 0
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\n":
            if index > start:
                spans.append((start, index))
            start = index + 1
            index += 1
            continue
        if char in ".!?":
            boundary = _sentence_boundary(text, index)
            if boundary is not None:
                end, following = boundary
                spans.append((start, end))
                start = following
                index = following
                continue
        index += 1
    if start < length:
        spans.append((start, length))
    return tuple(spans)


def _sentence_boundary(text: str, index: int) -> tuple[int, int] | None:
    """Return the end of the sentence at ``index`` and the start of the next one."""

    end = index + 1
    while end < len(text) and text[end] in "\"'\u201d\u2019)]\u00bb":
        end += 1
    following = end
    while following < len(text) and text[following].isspace():
        following += 1
    if following >= len(text):
        return end, following
    if text[following].islower():
        return None
    if text[index] == "." and _is_decimal_or_abbreviation(text, index):
        return None
    return end, following


def _is_decimal_or_abbreviation(text: str, index: int) -> bool:
    """Report whether the period at ``index`` ends a decimal or a known abbreviation."""

    if index > 0 and text[index - 1].isdigit():
        probe = index + 1
        if probe < len(text) and text[probe].isdigit():
            return True
    words = _trailing_words(text, index, 2)
    if len(words) == 2 and f"{words[0]} {words[1]}" in _ABBREVIATIONS:
        return True
    return len(words) == 1 and words[0] in _ABBREVIATIONS


def _trailing_words(text: str, index: int, count: int) -> tuple[str, ...]:
    """Return the last ``count`` alphabetic words before ``index``."""

    words: list[str] = []
    cursor = index
    while cursor > 0 and len(words) < count:
        cursor -= 1
        if not text[cursor].isalpha():
            continue
        end = cursor + 1
        while cursor > 0 and text[cursor - 1].isalpha():
            cursor -= 1
        words.append(text[cursor:end].lower())
    return tuple(reversed(words))
