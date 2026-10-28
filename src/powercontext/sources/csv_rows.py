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

"""Row-level CSV Sources captured by primary key for exact evidence recall."""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

from pydantic import BaseModel

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.models import Source, SourceMaterialization

CSV_ROW_SOURCE_NAME = "csv-row"
_CSV_ROW_IDENTITY_PREFIX = "csv:"


class CSVDocumentInput(BaseModel):
    """One offline CSV document offered for row-level capture."""

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One captured CSV row addressed by its document and primary key."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


class CSVRowSource(Source):
    """A captured CSV row that stays addressable after rows are reordered."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def _validated_document_id(document_id: str) -> str:
    if not document_id or document_id != document_id.strip():
        raise ValueError("document_id must be a non-empty string without surrounding whitespace")  # noqa: TRY003
    return document_id


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build the stable identity of one captured CSV row."""

    document_id = _validated_document_id(document_id)
    if not primary_key_value:
        raise ValueError("primary_key_value must not be empty")  # noqa: TRY003
    identity = f"{_CSV_ROW_IDENTITY_PREFIX}{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(identity) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"CSV row identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return identity


def _decode_csv_records(content: bytes) -> list[list[str]]:
    """Decode one UTF-8 CSV document into its raw records."""

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("CSV content must be valid UTF-8") from error  # noqa: TRY003
    try:
        return list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error as error:
        raise ValueError(f"CSV content is malformed: {error}") from error  # noqa: TRY003


def _validated_header(header: list[str], primary_key: str) -> int:
    """Validate the header row and return the primary key column index."""

    if not header:
        raise ValueError("CSV header row must not be empty")  # noqa: TRY003
    for name in header:
        if not name.strip():
            raise ValueError("CSV header names must not be empty or whitespace only")  # noqa: TRY003
    if len(set(header)) != len(header):
        raise ValueError("CSV header names must be unique")  # noqa: TRY003
    if primary_key not in header:
        raise ValueError("primary_key must match one CSV header name exactly")  # noqa: TRY003
    return header.index(primary_key)


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse one offline CSV document into its row-level capture inputs."""

    document_id = _validated_document_id(value.document_id)
    primary_key = value.primary_key
    if not primary_key:
        raise ValueError("primary_key must not be empty")  # noqa: TRY003
    records = _decode_csv_records(value.content)
    if not records:
        raise ValueError("CSV document must contain a header row")  # noqa: TRY003
    header = records[0]
    key_index = _validated_header(header, primary_key)
    width = len(header)
    columns = tuple(header)
    rows: list[CSVRowInput] = []
    seen: set[str] = set()
    for record in records[1:]:
        if not record:
            continue
        if len(record) != width:
            raise ValueError("CSV row width must match the header width")  # noqa: TRY003
        key_value = record[key_index]
        if not key_value.strip():
            raise ValueError("CSV primary key value must not be empty")  # noqa: TRY003
        if key_value in seen:
            raise ValueError("CSV primary key values must be unique within one document")  # noqa: TRY003
        seen.add(key_value)
        rows.append(
            CSVRowInput(
                document_id=document_id,
                primary_key=primary_key,
                primary_key_value=key_value,
                columns=columns,
                values=tuple(record),
            )
        )
    return tuple(rows)


class CSVRowSourceAdapter:
    """Resolve and read the row-level CSV Source."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    """Expose one captured CSV row as standard textual evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


CSV_ROW_SOURCE_ADAPTER = CSVRowSourceAdapter()
CSV_ROW_SOURCE_DEFINITION = AdapterSourceDefinition(
    CSV_ROW_SOURCE_ADAPTER,
    projections=(CSVRowTextEvidenceProjection(),),
)
