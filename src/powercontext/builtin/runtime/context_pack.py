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

"""Context Pack wire format and prefix-reuse delta protocol."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass

CONTEXT_PACK_SCHEMA = "powercontext.context-pack.v1"
CONTEXT_PACK_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"
DEFAULT_CHUNK_BYTES = 512

_PACK_FIELDS = frozenset({"schema", "items", "digest", "byte_count"})
_DELTA_FIELDS = frozenset({"schema", "base_digest", "target_digest", "prefix_items", "appended"})


class ContextPackError(ValueError):
    """Raised when a Context Pack or delta violates the wire protocol."""


def _canonical_json(payload: dict) -> bytes:
    """Serialize one wire payload with sorted keys and no extra whitespace."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encode_items(items: tuple[bytes, ...]) -> list[str]:
    """Encode chunk bytes as standard Base64 strings."""

    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode_items(values: object, label: str) -> tuple[bytes, ...]:
    """Decode one wire chunk list into bytes."""

    if not isinstance(values, list):
        raise ContextPackError(f"{label} chunk list must be an array")  # noqa: TRY003
    items: list[bytes] = []
    for value in values:
        if not isinstance(value, str):
            raise ContextPackError(f"{label} chunk must be a Base64 string")  # noqa: TRY003
        try:
            items.append(base64.b64decode(value, validate=True))
        except (binascii.Error, ValueError) as error:
            raise ContextPackError(f"{label} chunk is not valid Base64") from error  # noqa: TRY003
    return tuple(items)


def _pack_digest(items: tuple[bytes, ...]) -> str:
    """Return the digest of one pack over its schema and chunk list."""

    unsigned = {"schema": CONTEXT_PACK_SCHEMA, "items": _encode_items(items)}
    return f"sha256:{hashlib.sha256(_canonical_json(unsigned)).hexdigest()}"


def _decode_payload(data: bytes, fields: frozenset[str], label: str) -> dict:
    """Decode one wire payload and require exactly the declared fields."""

    try:
        payload = json.loads(data.decode("utf-8"))
    except ValueError as error:
        raise ContextPackError(f"{label} payload is not valid UTF-8 JSON") from error  # noqa: TRY003
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ContextPackError(f"{label} payload must contain exactly {sorted(fields)}")  # noqa: TRY003
    return payload


def _require_chunk_limit(chunk_bytes: int) -> int:
    """Validate the caller supplied chunk byte limit."""

    if isinstance(chunk_bytes, bool) or not isinstance(chunk_bytes, int):
        raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003
    if chunk_bytes < 1:
        raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003
    return chunk_bytes


def _content_bytes(content: str | bytes) -> bytes:
    """Return the UTF-8 bytes of one context payload."""

    if isinstance(content, bytes):
        try:
            return content.decode("utf-8").encode("utf-8")
        except UnicodeDecodeError as error:
            raise ContextPackError("context content must be valid UTF-8") from error  # noqa: TRY003
    if isinstance(content, str):
        return content.encode("utf-8")
    raise ContextPackError("context content must be text or bytes")  # noqa: TRY003


def _chunk_bytes(data: bytes, chunk_bytes: int) -> tuple[bytes, ...]:
    """Split raw bytes into deterministic chunks of at most the byte limit.

    Every chunk stays valid UTF-8 on its own, so a receiver can decode one
    chunk without the bytes that follow it.
    """

    if not data:
        return ()
    items: list[bytes] = []
    current = bytearray()
    for character in data.decode("utf-8"):
        encoded = character.encode("utf-8")
        if len(current) + len(encoded) > chunk_bytes:
            if not current:
                raise ContextPackError("one character exceeds the chunk byte limit")  # noqa: TRY003
            items.append(bytes(current))
            current = bytearray()
        current += encoded
    if current:
        items.append(bytes(current))
    return tuple(items)


def _common_prefix_length(base: tuple[bytes, ...], target: tuple[bytes, ...]) -> int:
    """Return how many leading chunks both chunk sequences share."""

    limit = min(len(base), len(target))
    prefix = 0
    while prefix < limit and base[prefix] == target[prefix]:
        prefix += 1
    return prefix


@dataclass(frozen=True, slots=True)
class ContextPack:
    """One complete context payload split into deterministic chunks."""

    items: tuple[bytes, ...]
    digest: str

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        """Build one complete pack from its chunk bytes."""

        chunks = tuple(bytes(item) for item in items)
        return cls(items=chunks, digest=_pack_digest(chunks))

    @classmethod
    def from_content(cls, content: str | bytes, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> ContextPack:
        """Build one complete pack by chunking the supplied context text."""

        limit = _require_chunk_limit(chunk_bytes)
        return cls.build(_chunk_bytes(_content_bytes(content), limit))

    @property
    def byte_count(self) -> int:
        """Return the exact serialized size of this pack, including its own field."""

        return len(self.to_bytes())

    @property
    def content_bytes(self) -> bytes:
        """Return the exact context bytes this pack carries."""

        return b"".join(self.items)

    def to_bytes(self) -> bytes:
        """Serialize this pack into its wire representation."""

        unsigned = {"schema": CONTEXT_PACK_SCHEMA, "items": _encode_items(self.items)}
        byte_count = 0
        for _ in range(8):
            payload = {**unsigned, "digest": self.digest, "byte_count": byte_count}
            encoded = _canonical_json(payload)
            if len(encoded) == byte_count:
                return encoded
            byte_count = len(encoded)
        raise ContextPackError("pack wire size did not converge")  # noqa: TRY003

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        """Decode and validate one complete pack."""

        payload = _decode_payload(data, _PACK_FIELDS, "pack")
        if payload["schema"] != CONTEXT_PACK_SCHEMA:
            raise ContextPackError("pack schema is not supported")  # noqa: TRY003
        items = _decode_items(payload["items"], "pack")
        digest = _pack_digest(items)
        if payload["digest"] != digest:
            raise ContextPackError("pack digest does not match its chunks")  # noqa: TRY003
        byte_count = payload["byte_count"]
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0:
            raise ContextPackError("pack byte_count must be a non-negative integer")  # noqa: TRY003
        pack = cls(items=items, digest=digest)
        if byte_count != pack.byte_count:
            raise ContextPackError("pack byte_count does not match its wire size")  # noqa: TRY003
        return pack


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """One incremental transfer that reuses a base pack prefix."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        """Build the delta that turns one base pack into one target pack.

        Any valid shared prefix may be reused, so the delta keeps every leading
        chunk the two packs already share and carries the rest of the target.
        """

        prefix = _common_prefix_length(base.items, target.items)
        return cls(
            base_digest=base.digest,
            target_digest=target.digest,
            prefix_items=prefix,
            appended=tuple(target.items[prefix:]),
        )

    @property
    def byte_count(self) -> int:
        """Return the exact serialized size of this delta."""

        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        """Serialize this delta into its wire representation."""

        payload = {
            "schema": CONTEXT_PACK_DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _encode_items(self.appended),
        }
        return _canonical_json(payload)

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        """Decode and validate one delta."""

        payload = _decode_payload(data, _DELTA_FIELDS, "delta")
        if payload["schema"] != CONTEXT_PACK_DELTA_SCHEMA:
            raise ContextPackError("delta schema is not supported")  # noqa: TRY003
        prefix_items = payload["prefix_items"]
        if not isinstance(prefix_items, int) or isinstance(prefix_items, bool) or prefix_items < 0:
            raise ContextPackError("delta prefix_items must be a non-negative integer")  # noqa: TRY003
        for field in ("base_digest", "target_digest"):
            value = payload[field]
            if not isinstance(value, str) or not value:
                raise ContextPackError(f"delta {field} must be a non-empty string")  # noqa: TRY003
        return cls(
            base_digest=payload["base_digest"],
            target_digest=payload["target_digest"],
            prefix_items=prefix_items,
            appended=_decode_items(payload["appended"], "delta"),
        )


def align_context_pack(base: ContextPack, content: str | bytes, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES) -> ContextPack:
    """Build the target pack that reuses the base pack's chunk boundaries.

    The longest common byte prefix of both payloads decides how many base
    chunks stay valid. The target keeps those base chunks verbatim and only
    re-chunks the bytes that follow, so a receiver can rebuild the target from
    the base pack and the resulting delta alone.
    """

    limit = _require_chunk_limit(chunk_bytes)
    target_bytes = _content_bytes(content)
    base_bytes = base.content_bytes
    common = 0
    shared = min(len(base_bytes), len(target_bytes))
    while common < shared and base_bytes[common] == target_bytes[common]:
        common += 1
    prefix_items = 0
    consumed = 0
    for item in base.items:
        if consumed + len(item) > common:
            break
        consumed += len(item)
        prefix_items += 1
    items = tuple(base.items[:prefix_items]) + _chunk_bytes(target_bytes[consumed:], limit)
    return ContextPack.build(items)


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Rebuild the target pack from one base pack and one delta."""

    if base.digest != delta.base_digest:
        raise ContextPackError("delta base digest does not match the base pack")  # noqa: TRY003
    if delta.prefix_items > len(base.items):
        raise ContextPackError("delta prefix_items exceeds the base pack chunk count")  # noqa: TRY003
    items = tuple(base.items[: delta.prefix_items]) + tuple(delta.appended)
    target = ContextPack.build(items)
    if target.digest != delta.target_digest:
        raise ContextPackError("delta target digest does not match the rebuilt pack")  # noqa: TRY003
    return target


__all__ = [
    "CONTEXT_PACK_DELTA_SCHEMA",
    "CONTEXT_PACK_SCHEMA",
    "DEFAULT_CHUNK_BYTES",
    "ContextPack",
    "ContextPackDelta",
    "ContextPackError",
    "align_context_pack",
    "apply_context_pack_delta",
]
