from __future__ import annotations

import base64
from typing import Never

import httpx

from .base import VisionParseError, VisionResult, VisionUnavailable
from .prompts import PROMPT, parse_result

USER_TEXT = "Analyze this CCTV frame."


class OpenAIVision:
    """Analyse CCTV frames through an OpenAI-compatible chat endpoint."""

    name = "openai"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 60.0,
        max_tokens: int = 160,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url
        self.model = model
        self.max_tokens = max_tokens
        self._api_key = api_key
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._client = httpx.Client(timeout=timeout, transport=transport)
        self._last_error: str | None = None

    def analyze(self, jpeg: bytes, prompt: str) -> VisionResult:
        try:
            response = self._client.post(
                self._endpoint,
                json=self._payload(jpeg),
                headers=self._headers(),
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            self._fail(f"Vision endpoint returned HTTP {error.response.status_code}")
        except httpx.HTTPError as error:
            self._fail(f"Vision endpoint request failed ({type(error).__name__})")

        try:
            result = parse_result(self._content(response))
        except VisionParseError as error:
            self._last_error = f"Vision endpoint returned unparseable content: {error}"
            raise

        self._last_error = None
        return result

    def status(self) -> dict[str, object]:
        return {
            "name": self.name,
            "model": self.model,
            "base_url": self.base_url,
            "available": True,
            "error": self._last_error,
        }

    def _payload(self, jpeg: bytes) -> dict[str, object]:
        encoded = base64.b64encode(jpeg).decode("ascii")
        return {
            "model": self.model,
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": USER_TEXT},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                        },
                    ],
                },
            ],
        }

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _content(self, response: httpx.Response) -> str:
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            self._fail("Vision endpoint returned an invalid chat completion", error)
        if not isinstance(content, str):
            self._fail("Vision endpoint returned no message content")
        return content

    def _fail(self, message: str, error: BaseException | None = None) -> Never:
        self._last_error = message
        raise VisionUnavailable(message) from error
