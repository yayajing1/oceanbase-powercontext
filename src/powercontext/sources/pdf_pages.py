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
import math
import re
from collections import Counter
from collections.abc import Callable
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
_DISCOVERY_LIMIT = 60
_PAGE_SWEEP_LIMIT = 4096
_DISCOVERY_QUERIES = 10
_HEAD_CHARS = 220
_LEXICAL_WEIGHT = 1.0
_EXPANDED_WEIGHT = 0.5
_TABLE_BONUS = 1.858
_MIN_SLOTS = 3

_TOKEN = re.compile(r"[a-z0-9]+")
_CAPITAL_TOKEN = re.compile(r"[A-Z][A-Za-z0-9&.'-]*")
_SECTION_MARKER = re.compile(
    r"item\s*(1a|1b|1|2|3|4|5|6|7a|7|8|9a|9|9b|10|11|12|13|14|15)\s*\.\s*"
    r"([a-z][a-z ,'&/-]{0,55})",
    re.IGNORECASE,
)
_ANY_ITEM = re.compile(r"\bitem\s*\d+[a-z]?\s*\.", re.IGNORECASE)
_SECTION_NAMES = {
    "1": "BUSINESS",
    "1a": "RISK",
    "1b": "RISK",
    "2": "PROPERTIES",
    "3": "LEGAL",
    "4": "OTHER",
    "5": "MARKET",
    "6": "DATA",
    "7": "MDNA",
    "7a": "MARKET_RISK",
    "8": "FINANCIAL",
    "9": "OTHER",
    "9a": "CONTROLS",
    "9b": "OTHER",
    "10": "OTHER",
    "11": "OTHER",
    "12": "OTHER",
    "13": "OTHER",
    "14": "OTHER",
    "15": "EXHIBITS",
}
_STATEMENT_TITLES = {
    "INCOME": (
        "consolidated statements of operations",
        "consolidated statement of operations",
        "consolidated statement of income",
        "consolidated statements of income",
        "consolidated statements of earnings",
    ),
    "BALANCE": ("consolidated balance sheet", "consolidated balance sheets"),
    "CASHFLOW": (
        "consolidated statements of cash flows",
        "consolidated statement of cash flows",
        "consolidated statement of cash flow",
    ),
}
_MDNA_TITLES = (
    "management's discussion and analysis",
    "results of operations",
    "liquidity and capital resources",
    "critical accounting estimates",
    "consolidated results of operations",
)
_SEGMENT_MARKERS = (
    "by business segment",
    "business segment",
    "reportable segment",
    "operating segment",
    "segment information",
    "segment sales",
    "segment data",
    "by segment",
)
_SEGMENT_TABLE_MARKERS = (
    "by business segment",
    "business segment information",
    "business segment data",
)
_LEGAL_MARKERS = ("legal proceeding", "legal proceedings", "litigation")
_NOTE_MARKERS = (
    "notes to the consolidated",
    "notes to consolidated financial statements",
    "recently issued accounting pronouncements",
)
_INTENT_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        (
            "balance sheet",
            "liquidity",
            "quick ratio",
            "current ratio",
            "dpo",
            "days payable",
            "days sales outstanding",
            "accounts payable",
            "accounts receivable",
            "inventory",
            "inventories",
            "working capital",
            "total assets",
            "total liabilities",
            "shareholders' equity",
            "stockholders' equity",
        ),
        ("BALANCE",),
    ),
    (
        (
            "revenue",
            "margin",
            "ebitda",
            "tax rate",
            "gross profit",
            "operating income",
            "earnings",
            "income statement",
            "statement of income",
            "net income",
            "cost of sales",
            "net sales",
            "p&l",
            "cogs",
            "year-over-year change in revenue",
        ),
        ("INCOME",),
    ),
    (
        (
            "cash flow",
            "capex",
            "capital expenditure",
            "depreciation",
            "amortization",
            "operating activities",
            "investing activities",
            "financing activities",
            "free cash flow",
        ),
        ("CASHFLOW",),
    ),
    (("segment", "product categor", "service categor"), ("SEGMENT",)),
    (("legal", "litigation", "lawsuit", "proceeding", "court", "battle"), ("LEGAL",)),
    (("restructuring", "accounting polic", "contingenc", "commitment"), ("NOTES",)),
    (
        (
            "products",
            "services",
            "customer",
            "geograph",
            "business",
            "cyclical",
            "production",
            "capacity",
            "backlog",
            "market",
            "employee",
            "supplier",
            "manufactur",
            "airline",
            "industry",
            "operates",
        ),
        ("BUSINESS",),
    ),
    (("concentration", "risk", "depend", "material adverse"), ("RISK",)),
    (("drove", "results of operations", "change as of", "compare", "driver"), ("MDNA",)),
)
_EXTRA_INTENT: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("capital intensive", "capital-intensive"), ("BALANCE", "INCOME", "CASHFLOW")),
    (("dpo", "days payable"), ("BALANCE", "INCOME")),
)
_SYNONYMS: dict[str, tuple[str, ...]] = {
    "geograph": (
        "region",
        "north america",
        "latin america",
        "emea",
        "apac",
        "africa",
        "middle east",
        "south asia",
        "asia pacific",
        "australia",
        "china",
        "europe",
    ),
    "customer": ("client",),
    "product": ("offering", "portfolio"),
    "cyclical": ("cycle", "seasonal", "volatile"),
    "production": ("manufacturing", "delivery"),
    "restructuring": ("reorganization", "impairment"),
    "legal": ("litigation", "proceeding"),
    "margin": ("profitability",),
    "ebitda": (),
    "capex": ("capital expenditure", "purchases of property"),
}
_QUESTION_WORDS = frozenset({
    "a",
    "an",
    "among",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "for",
    "from",
    "had",
    "has",
    "have",
    "how",
    "if",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "should",
    "that",
    "the",
    "this",
    "to",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "would",
})
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

    words = _TOKEN.findall(question.lower())
    kept = [
        word for word in words if len(word) > 1 and word not in _QUERY_FUNCTION_WORDS and word not in _QUERY_NOISE_WORDS
    ]
    return tuple(dict.fromkeys(kept))


def _evidence_stems(terms: tuple[str, ...]) -> frozenset[str]:
    """Return the stems a stored page may answer one question with.

    A question writes ``FY2022`` while the report prints ``2022`` inside
    ``years ended december 31, 2022``. The fiscal year therefore counts as
    evidence for the page that reports that year, next to the morphological
    stems of the ordinary words.
    """

    stems = {_stem(token) for token in terms}
    for token in terms:
        if token.startswith("fy") and len(token) > 2 and token[2:].isdigit():
            stems.add(token[2:])
    return frozenset(stems)


def _stem(token: str) -> str:
    """Return the light morphological stem of one lowercase token.

    The Memory index keeps surface forms, so a question that asks about
    ``revenue`` never matches a page that only says ``revenues``. Stemming both
    sides of the comparison recovers those pages without touching the index.
    """

    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("es") and token[-3] in "sxzo":
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _expanded_terms(terms: tuple[str, ...]) -> frozenset[str]:
    """Return the question stems plus the report wording that answers them.

    A question asks for the ``geographies`` a company operates in while the
    report enumerates ``North America``, ``Latin America`` and ``emea``. The
    domain vocabulary that names the same fact is added next to the literal
    stems, which lets a page that never repeats the question's own word still
    be recognised as the answer.
    """

    expanded: set[str] = set()
    for term in terms:
        stem = _stem(term)
        expanded.add(stem)
        for marker, values in _SYNONYMS.items():
            if marker in stem:
                for value in values:
                    expanded.update(_stem(token) for token in value.split())
    return frozenset(expanded)


def _discovery_queries(question: str) -> tuple[tuple[str, float], ...]:
    """Return the queries that reveal which report a question is about.

    The report a question belongs to is named in the question itself, so the
    capitalised words are the reliable handle: ``PepsiCo``, ``AMD`` or
    ``Boeing``. Those single-word queries admit every page that mentions the
    name, and their hits reveal the document identifier that prefixes each
    stored page.
    """

    queries: list[tuple[str, float]] = []
    seen: set[str] = set()

    def add(value: str, weight: float) -> None:
        text = " ".join(value.split())
        if not text or text.lower() in seen:
            return
        seen.add(text.lower())
        queries.append((text, weight))

    add(question, 0.0)
    for token in _CAPITAL_TOKEN.findall(question):
        if token.lower() in _QUESTION_WORDS or len(token) < 2:
            continue
        add(token, 1.0)
    for term in _query_terms(question):
        if len(term) > 3:
            add(term, 0.05)
    return tuple(queries[:_DISCOVERY_QUERIES])


def _pick_document(
    question: str,
    documents: Counter[str],
    votes: Counter[str],
) -> str | None:
    """Return the report identifier that the question is about."""

    if not documents:
        return None
    identity = {_stem(token) for token in _query_terms(question)}
    best: str | None = None
    best_key: tuple[float, int] | None = None
    for identifier in documents:
        tokens = {_stem(token) for token in _TOKEN.findall(identifier.lower())}
        score = 2.0 * len(identity & tokens) + votes.get(identifier, 0.0)
        key = (score, documents[identifier])
        if best_key is None or key > best_key:
            best, best_key = identifier, key
    return best


async def _collect_pages(
    memory: _PageSearchMemory,
    question: str,
) -> tuple[dict[str, object], str | None]:
    """Search the Memory for the pages of the report the question is about.

    The question names its report, so a short discovery search locates the
    report identifier that prefixes every stored page of that report. Asking
    the Memory for that identifier returns the complete page set of the
    report, which is what the ranking needs in order to know the structure of
    the report and to place the question inside it.
    """

    hits: dict[str, object] = {}
    documents: Counter[str] = Counter()
    votes: Counter[str] = Counter()
    for query, weight in _discovery_queries(question):
        page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=_DISCOVERY_LIMIT))
        found: Counter[str] = Counter()
        for hit in page.hits:
            hits.setdefault(hit.entry_id, hit)
            identifier = hit.text.partition("\n")[0]
            documents[identifier] += 1
            found[identifier] += 1
        if weight > 0.0 and len(found) == 1:
            votes[next(iter(found))] += weight
    target = _pick_document(question, documents, votes)
    if target is not None:
        page = await memory.search(SearchMemoryRequest(query=target, mode="fts", limit=_PAGE_SWEEP_LIMIT))
        for hit in page.hits:
            hits.setdefault(hit.entry_id, hit)
    return hits, target


def _section_starts(pages: dict[int, str]) -> tuple[list[tuple[int, str]], set[int]]:
    """Return the pages that open a section and the index pages of one report."""

    starts: list[tuple[int, str]] = []
    index_pages: set[int] = set()
    for number in sorted(pages):
        head = pages[number][:_HEAD_CHARS].lower()
        if len(_ANY_ITEM.findall(head)) >= 4:
            index_pages.add(number)
            continue
        match = _SECTION_MARKER.search(head)
        if match is None:
            continue
        before = head[: match.start()].rstrip()
        if before and not before.lower().endswith(("contents", "part i", "part ii", "part iii", "part iv")):
            continue
        after = head[match.end() :].lstrip()
        if after.split(" ", 1)[0].strip(".,;:").isdigit():
            continue
        name = _SECTION_NAMES.get(match.group(1).lower())
        if name is not None:
            starts.append((number, name))
    return starts, index_pages


def _section_map(pages: dict[int, str]) -> dict[int, str]:
    """Assign every page of one report to the section that contains it.

    A report announces its sections with numbered item headings. The heading
    that opens a section marks the first page of that section, and every later
    page belongs to the section that opened most recently. The index pages that
    list all headings at once are recognised by their density of headings and
    are skipped, so the first page after the index opens the business section.
    """

    starts, index_pages = _section_starts(pages)
    ordered = sorted(pages)
    mapping: dict[int, str] = {}
    current = "FRONT"
    position = 0
    for number in ordered:
        while position < len(starts) and starts[position][0] <= number:
            current = starts[position][1]
            position += 1
        mapping[number] = current
    if "BUSINESS" not in mapping.values():
        first = starts[0][0] if starts else None
        for number in ordered:
            if first is not None and number >= first:
                break
            if number in index_pages:
                continue
            mapping[number] = "BUSINESS"
    return mapping


def _page_kinds(head: str, body: str) -> frozenset[str]:
    """Return the kinds of evidence one stored page can answer.

    A primary statement prints the figures a question asks for, the management
    discussion explains them, the business section describes the company, a
    segment note splits the totals up and a legal note reports the disputes.
    The page announces its kind in its own heading, so the kinds are read from
    the page instead of guessed from the wording of the question.
    """

    kinds: set[str] = set()
    for kind, titles in _STATEMENT_TITLES.items():
        if any(title in head for title in titles):
            kinds.add(kind)
    if any(title in head for title in _MDNA_TITLES):
        kinds.add("MDNA")
    if any(marker in body for marker in _SEGMENT_MARKERS):
        kinds.add("SEGMENT")
        if any(marker in head for marker in _SEGMENT_TABLE_MARKERS):
            kinds.add("SEGMENT_TABLE")
    if any(marker in body for marker in _LEGAL_MARKERS):
        kinds.add("LEGAL")
    if any(marker in head for marker in _NOTE_MARKERS):
        kinds.add("NOTES")
    return frozenset(kinds)


def _intent_targets(question: str) -> list[tuple[str, float]]:
    """Return the page kinds that can answer one question."""

    lowered = question.lower()
    scores: dict[str, float] = {}
    for keys, kinds in _INTENT_RULES:
        if any(key in lowered for key in keys):
            for kind in kinds:
                scores[kind] = scores.get(kind, 0.0) + 1.0
    for keys, kinds in _EXTRA_INTENT:
        if any(key in lowered for key in keys):
            for kind in kinds:
                scores[kind] = scores.get(kind, 0.0) + 1.0
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def _lexical_scores(question: str, tokens: dict[str, frozenset[str]]) -> dict[str, float]:
    """Score every page of one report by the question words it carries."""

    terms = _query_terms(question)
    wanted = _evidence_stems(terms)
    expanded = _expanded_terms(terms)
    frequency: Counter[str] = Counter()
    for value in tokens.values():
        frequency.update(value)
    total = max(1, len(tokens))

    def weight(token: str) -> float:
        return math.log(1.0 + total / (1.0 + frequency.get(token, 0)))

    lexical: dict[str, float] = {}
    for key in tokens:
        base = sum(weight(token) for token in wanted if token in tokens[key])
        extra = sum(weight(token) for token in expanded if token in tokens[key])
        lexical[key] = _LEXICAL_WEIGHT * base + _EXPANDED_WEIGHT * extra
    peak = max(lexical.values(), default=1.0) or 1.0
    return {key: value / peak for key, value in lexical.items()}


def _kind_pages(
    members: list[str],
    allowance: int,
    lexical: dict[str, float],
    bonus: Callable[[str], float],
    taken: set[str],
) -> list[str]:
    """Return the best pages of one kind that are still unselected."""

    ordered = sorted(members, key=lambda key: (-(lexical[key] + bonus(key)), key))
    chosen: list[str] = []
    for key in ordered:
        if len(chosen) >= allowance:
            break
        if key in taken:
            continue
        taken.add(key)
        chosen.append(key)
    return chosen


def _select_pages(
    question: str,
    pages: dict[str, str],
    kinds: dict[str, frozenset[str]],
    lexical: dict[str, float],
    limit: int,
) -> list[str]:
    """Spend the returned slots over the page kinds that can answer the question."""

    def bonus(key: str) -> float:
        return _TABLE_BONUS if "SEGMENT_TABLE" in kinds[key] else 0.0

    selected: list[str] = []
    taken: set[str] = set()
    targets = _intent_targets(question)
    if targets:
        total_weight = sum(share for _, share in targets)
        for kind, share in targets:
            members = [key for key in pages if kind in kinds[key]]
            if not members:
                continue
            allowance = max(_MIN_SLOTS, round(limit * share / total_weight))
            allowance = min(allowance, len(members))
            selected.extend(_kind_pages(members, allowance, lexical, bonus, taken))
    for key in sorted(pages, key=lambda key: (-(lexical[key] + bonus(key)), key)):
        if len(selected) >= limit:
            break
        if key in taken:
            continue
        taken.add(key)
        selected.append(key)
    return selected[:limit]


def _rank_pages(
    question: str,
    pages: dict[str, str],
    numbers: dict[str, int],
    limit: int,
) -> list[str]:
    """Order the pages of one report by the evidence they carry for a question.

    Two signals decide the order. The structural signal reads the page kind
    from the page and spends the returned slots over the kinds the question can
    be answered by, so a question about a margin reaches the statement that
    prints it. The lexical signal orders the pages inside one kind by the
    question words they carry, weighted by how rare those words are in the
    report.
    """

    if not pages:
        return []
    heads: dict[str, str] = {}
    bodies: dict[str, str] = {}
    tokens: dict[str, frozenset[str]] = {}
    for key, text in pages.items():
        _, _, body = text.partition("\n")
        body = " ".join(body.split())
        bodies[key] = body
        heads[key] = body[:_HEAD_CHARS].lower()
        tokens[key] = frozenset(_stem(token) for token in _TOKEN.findall(body.lower()))
    lexical = _lexical_scores(question, tokens)
    sections = _section_map({number: heads[key] for key, number in numbers.items()})
    kinds: dict[str, frozenset[str]] = {}
    for key in pages:
        found = set(_page_kinds(heads[key], bodies[key].lower()))
        section = sections.get(numbers.get(key, 0), "FRONT")
        if section == "BUSINESS":
            found.add("BUSINESS")
        if section == "RISK":
            found.add("RISK")
        kinds[key] = frozenset(found)
    return _select_pages(question, pages, kinds, lexical, limit)


def _page_number(source_id: str) -> int | None:
    """Return the physical page number encoded in one PDF page Source identity."""

    tail = source_id.rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else None


async def _resolve_pages(
    memory: _PageSearchMemory,
    hits: list[object],
) -> dict[str, int]:
    """Resolve candidate entries into the physical page number they carry."""

    resolved: dict[str, int] = {}
    for hit in hits:
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        entry = await memory.get(GetMemoryEntryRequest(citation=citation))
        references = entry.entry.sources
        if not references:
            continue
        page_number = _page_number(references[0].source_id)
        if page_number is not None:
            resolved[hit.entry_id] = page_number
    return resolved


async def search_pdf_pages(
    memory: _PageSearchMemory,
    question: str,
    limit: int = 10,
) -> tuple[MemoryCitation, ...]:
    """Search one scoped Memory for the pages that answer one natural-language question."""

    if limit < 1:
        return ()
    pool, target = await _collect_pages(memory, question)
    if not pool or target is None:
        return ()
    pages = {key: hit.text for key, hit in pool.items() if hit.text.partition("\n")[0] == target}
    if not pages:
        return ()
    numbers = await _resolve_pages(memory, [pool[key] for key in pages])
    ordered = _rank_pages(question, pages, numbers, limit)
    citations: list[MemoryCitation] = []
    seen: set[tuple[str, int]] = set()
    for key in ordered:
        number = numbers.get(key)
        if number is None or (target, number) in seen:
            continue
        seen.add((target, number))
        hit = pool[key]
        citations.append(
            MemoryCitation(
                memory_ref=hit.memory_ref,
                entry_id=hit.entry_id,
                entry_version_id=hit.entry_version_id,
            )
        )
        if len(citations) >= limit:
            break
    return tuple(citations)
