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

"""Page-level PDF Sources captured for exact financial-report evidence recall."""

from __future__ import annotations

import hashlib
import itertools
import re
from io import BytesIO
from typing import Protocol
from urllib.parse import quote

from pydantic import BaseModel
from pypdf import PdfReader

from powercontext.builtin.artifacts.memory import MemoryCitation
from powercontext.builtin.runtime import (
    GetMemoryEntryRequest,
    MemoryEntryVersion,
    MemorySearchPage,
    SearchMemoryRequest,
)
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    TextEvidence,
)
from powercontext.sources.models import Source, SourceMaterialization

PDF_PAGE_SOURCE_NAME = "pdf-page"

_PDF_PAGE_IDENTITY_PREFIX = "pdf:"
_SEARCH_FANOUT = 3
_SEARCH_MIN_CANDIDATES = 60
_SEARCH_RRF_CONSTANT = 60
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.?!])\s+|\n+")
_QUERY_TERM = re.compile(r"[a-z0-9]+")
_QUERY_FUNCTION_WORDS = frozenset({
    "a",
    "an",
    "the",
    "and",
    "or",
    "for",
    "of",
    "to",
    "in",
    "on",
    "at",
    "by",
    "from",
    "with",
    "as",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "what",
    "which",
    "who",
    "how",
    "why",
    "when",
    "where",
    "do",
    "does",
    "did",
    "can",
    "could",
    "should",
    "would",
    "i",
    "we",
    "you",
    "it",
    "its",
    "our",
    "your",
    "this",
    "that",
    "these",
    "those",
    "please",
})
_QUERY_NOISE_WORDS = frozenset({
    "also",
    "analyst",
    "answer",
    "based",
    "calculate",
    "can",
    "could",
    "define",
    "details",
    "directly",
    "explicitly",
    "from",
    "give",
    "if",
    "important",
    "include",
    "includes",
    "investment",
    "into",
    "materially",
    "note",
    "ongoing",
    "only",
    "operates",
    "outlined",
    "perspective",
    "primarily",
    "please",
    "quantity",
    "question",
    "relying",
    "reported",
    "respond",
    "response",
    "shown",
    "show",
    "shows",
    "state",
    "then",
    "there",
    "these",
    "this",
    "that",
    "use",
    "used",
    "using",
    "was",
    "were",
    "within",
    "would",
    "it",
    "its",
})


class PDFDocumentInput(BaseModel):
    """One complete original PDF offered for page-level capture."""

    document_id: str
    content: bytes


class PDFPageInput(BaseModel):
    """One captured physical page of a PDF document."""

    document_id: str
    document_sha256: str
    page_number: int
    text: str


class PDFPageSource(Source):
    """One captured PDF page addressed by its document, revision and page number."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


class _PageSearchMemory(Protocol):
    """The scoped Memory surface needed for page-level citation lookup."""

    async def search(self, request: SearchMemoryRequest, /) -> MemorySearchPage: ...

    async def get(self, request: GetMemoryEntryRequest, /) -> MemoryEntryVersion: ...


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Build the stable identity of one captured PDF page."""

    identity = f"{_PDF_PAGE_IDENTITY_PREFIX}{quote(document_id, safe='')}:{document_sha256}:{page_number}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        digest = hashlib.sha256(f"{document_id}\x00{document_sha256}\x00{page_number}".encode()).hexdigest()
        identity = f"{_PDF_PAGE_IDENTITY_PREFIX}{digest}"
    return identity


def _validated_document_id(document_id: str) -> str:
    """Validate the caller supplied document identifier."""

    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must be a non-empty string")  # noqa: TRY003
    return document_id


def _open_reader(content: bytes) -> PdfReader:
    """Open one PDF document, decrypting permission-only files without a password."""

    try:
        reader = PdfReader(BytesIO(content))
    except Exception as error:
        raise ValueError(f"PDF content is not a readable document: {error}") from error  # noqa: TRY003
    if reader.is_encrypted:
        try:
            outcome = reader.decrypt("")
        except Exception as error:
            raise ValueError(f"PDF content could not be decrypted: {error}") from error  # noqa: TRY003
        if not outcome:
            raise ValueError("PDF content requires a password")  # noqa: TRY003
    return reader


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Parse every physical page of one complete PDF document."""

    document_id = _validated_document_id(value.document_id)
    content = value.content
    if not content:
        raise ValueError("PDF content must not be empty")  # noqa: TRY003
    document_sha256 = hashlib.sha256(content).hexdigest()
    reader = _open_reader(content)
    pages: list[PDFPageInput] = []
    try:
        for page_number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            pages.append(
                PDFPageInput(
                    document_id=document_id,
                    document_sha256=document_sha256,
                    page_number=page_number,
                    text=text.rstrip(),
                )
            )
    except ValueError:
        raise
    except Exception as error:
        raise ValueError(f"PDF content could not be parsed: {error}") from error  # noqa: TRY003
    return tuple(pages)


class PDFPageSourceAdapter:
    """Resolve and read the page-level PDF Source."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Expose one captured PDF page as standard textual evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_ADAPTER = PDFPageSourceAdapter()
PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition(
    PDF_PAGE_SOURCE_ADAPTER,
    projections=(PDFPageTextEvidenceProjection(),),
)


def _query_terms(question: str) -> tuple[str, ...]:
    """Return the content words of one question, without meta-instruction vocabulary."""

    words = _QUERY_TERM.findall(question.lower())
    kept = [word for word in words if word not in _QUERY_FUNCTION_WORDS and word not in _QUERY_NOISE_WORDS]
    return tuple(dict.fromkeys(kept))


def _query_variants(question: str) -> tuple[str, ...]:
    """Return the offline query rewrites used to widen evidence-page recall.

    The whole question keeps the original phrasing, every sentence isolates one
    fact, and adjacent content-word pairs form short two-word queries. Short
    queries matter: the Memory lexical admission floor scales with the number of
    query terms, so a two-word query only demands one matched term and still
    reaches the page that answers the question.
    """

    variants: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        text = " ".join(value.split())
        if text and text not in seen:
            seen.add(text)
            variants.append(text)

    add(question)
    for sentence in _SENTENCE_BOUNDARY.split(question):
        add(sentence)
    terms = _query_terms(question)
    for left, right in itertools.pairwise(terms):
        add(f"{left} {right}")
    return tuple(variants)


async def _fuse_page_candidates(
    memory: _PageSearchMemory,
    variants: tuple[str, ...],
    limit: int,
) -> tuple[list[str], dict[str, object]]:
    """Search every query rewrite and fuse the results into one candidate order."""

    window = max(limit * _SEARCH_FANOUT, _SEARCH_MIN_CANDIDATES)
    quota = max(1, limit // len(variants))
    ordered: list[str] = []
    selected_entries: set[str] = set()
    fused: dict[str, float] = {}
    entry_of: dict[str, object] = {}
    for variant in variants:
        page = await memory.search(SearchMemoryRequest(query=variant, mode="fts", limit=window))
        taken = 0
        for rank, hit in enumerate(page.hits, start=1):
            fused[hit.entry_id] = fused.get(hit.entry_id, 0.0) + 1.0 / (_SEARCH_RRF_CONSTANT + rank)
            entry_of.setdefault(hit.entry_id, hit)
            if taken >= quota or hit.entry_id in selected_entries:
                continue
            selected_entries.add(hit.entry_id)
            ordered.append(hit.entry_id)
            taken += 1
    for key, _ in sorted(fused.items(), key=lambda item: -item[1]):
        if len(ordered) >= limit * 2:
            break
        if key not in selected_entries:
            selected_entries.add(key)
            ordered.append(key)
    return ordered, entry_of


async def _collect_page_citations(
    memory: _PageSearchMemory,
    ordered: list[str],
    entry_of: dict[str, object],
    limit: int,
) -> tuple[MemoryCitation, ...]:
    """Resolve candidate entries into exact citations of distinct pages."""

    citations: list[MemoryCitation] = []
    seen_sources: set[str] = set()
    for key in ordered:
        if len(citations) >= limit:
            break
        hit = entry_of[key]
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        entry = await memory.get(GetMemoryEntryRequest(citation=citation))
        references = entry.entry.sources
        if not references:
            continue
        source_id = references[0].source_id
        if source_id in seen_sources:
            continue
        seen_sources.add(source_id)
        citations.append(citation)
    return tuple(citations)


async def search_pdf_pages(
    memory: _PageSearchMemory,
    question: str,
    limit: int = 10,
) -> tuple[MemoryCitation, ...]:
    """Search one scoped Memory for the pages that answer one natural-language question."""

    if limit < 1:
        return ()
    variants = _query_variants(question) or (question,)
    ordered, entry_of = await _fuse_page_candidates(memory, variants, limit)
    return await _collect_page_citations(memory, ordered, entry_of, limit)
