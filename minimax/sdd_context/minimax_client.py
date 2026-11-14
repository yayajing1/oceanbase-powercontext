"""A small, dependency light client for the MiniMax API.

The MiniMax platform exposes an Anthropic compatible endpoint.  The client
talks to ``{base_url}/messages`` and sends the API key both as
``x-api-key`` and as ``Authorization: Bearer`` so that the same code works
with the platform keys (``sk-...``) and with the agent gateway tokens.

The API key is never written to disk and never printed; :meth:`transcript`
returns the request without the credential.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

__all__ = ["MiniMaxClient", "MiniMaxError", "MiniMaxResponse"]


class MiniMaxError(RuntimeError):
    """Raised when the MiniMax API rejects a request."""


@dataclass
class MiniMaxResponse:
    """One model answer plus the metadata needed for the run log."""

    model: str
    text: str
    usage: dict = field(default_factory=dict)
    request_id: str = ""
    elapsed_ms: int = 0
    raw: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "model": self.model,
            "usage": self.usage,
            "request_id": self.request_id,
            "elapsed_ms": self.elapsed_ms,
        }


class MiniMaxClient:
    """Call the MiniMax chat completion API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.minimaxi.com/anthropic",
        model: str = "MiniMax-M3",
        timeout: int = 300,
        max_tokens: int = 32768,
        thinking: str = "adaptive",
    ):
        if not api_key:
            raise MiniMaxError(
                "no MiniMax API key - set MINIMAX_API_KEY (see .env.example)"
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.thinking = thinking

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/messages"

    def _headers(self) -> dict[str, str]:
        return {
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": self.api_key,
            "authorization": f"Bearer {self.api_key}",
        }

    def complete(self, system: str, user: str, max_tokens: int | None = None) -> MiniMaxResponse:
        """Send one request and return the assistant answer."""
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or self.max_tokens,
            "messages": [{"role": "user", "content": user}],
        }
        if system:
            body["system"] = system
        if self.thinking:
            body["thinking"] = {"type": self.thinking}

        started = time.time()
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(self.endpoint, headers=self._headers(), json=body)
        elapsed_ms = int((time.time() - started) * 1000)

        if response.status_code != 200:
            raise MiniMaxError(
                f"MiniMax API returned {response.status_code}: {response.text[:800]}"
            )
        data = response.json()
        text = self._extract_text(data)
        return MiniMaxResponse(
            model=str(data.get("model", self.model)),
            text=text,
            usage=data.get("usage", {}) or {},
            request_id=str(data.get("id", "")),
            elapsed_ms=elapsed_ms,
            raw=data,
        )

    @staticmethod
    def _extract_text(data: dict) -> str:
        parts: list[str] = []
        for block in data.get("content", []) or []:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        if not parts:
            choices = data.get("choices") or []
            if choices:
                message = choices[0].get("message", {})
                parts.append(str(message.get("content", "")))
        return "\n".join(part for part in parts if part).strip()

    def transcript(self, system: str, user: str) -> dict:
        """A loggable description of a request (without the credential)."""
        return {
            "endpoint": self.endpoint,
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "user": user,
            "api_key": "***redacted***",
        }


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
