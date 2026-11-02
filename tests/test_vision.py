from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from netra.vision.base import VisionParseError, VisionResult, VisionUnavailable
from netra.vision.mock import MockVision
from netra.vision.openai import OpenAIVision
from netra.vision.prompts import PROMPT, parse_result

VALID_RESULT = (
    '{"event_type":"person","people_count":1,"vehicles_count":0,'
    '"description":"Visitor at the gate","confidence":0.9}'
)


def _completion(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _vision(
    handler: Callable[[httpx.Request], httpx.Response],
    **kwargs: Any,
) -> OpenAIVision:
    return OpenAIVision(
        "http://vision.local/v1",
        "test-model",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_mock_vision_is_deterministic() -> None:
    backend = MockVision()

    assert backend.name == "mock"
    assert backend.model == "mock"
    assert backend.analyze(b"frame", "prompt") == VisionResult(
        "person", 1, 0, "Mock detection", 0.5
    )
    assert backend.analyze(b"other-frame", "other-prompt") == backend.analyze(b"frame", "prompt")


def test_mock_vision_status() -> None:
    assert MockVision().status() == {
        "name": "mock",
        "model": "mock",
        "available": True,
        "error": None,
    }


def test_parse_result_accepts_fenced_and_plain_json() -> None:
    expected = VisionResult("person", 1, 0, "Visitor at the gate", 0.9)

    assert parse_result(VALID_RESULT) == expected
    assert parse_result(f"Here is the event:\n```json\n{VALID_RESULT}\n```") == expected


def test_parse_result_clamps_counts_and_confidence() -> None:
    result = parse_result(
        '{"event_type":"person","people_count":-3,"vehicles_count":250,'
        '"description":"Crowd","confidence":9.5}'
    )

    assert result.people_count == 0
    assert result.vehicles_count == 99
    assert result.confidence == 1.0


@pytest.mark.parametrize(
    "text",
    [
        "no json here",
        '{"event_type": "person",}',
        '{"event_type": "alien"}',
    ],
)
def test_parse_result_rejects_invalid_output(text: str) -> None:
    with pytest.raises(VisionParseError):
        parse_result(text)


def test_sends_openai_chat_completion_request() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return _completion(VALID_RESULT)

    result = _vision(handler, api_key="secret-key").analyze(b"frame-bytes", PROMPT)

    body = seen["body"]
    assert seen["path"] == "/v1/chat/completions"
    assert body["model"] == "test-model"
    assert body["temperature"] == 0
    assert body["max_tokens"] == 160
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["messages"][0] == {"role": "system", "content": PROMPT}

    user = body["messages"][1]
    assert user["role"] == "user"
    assert user["content"][0] == {"type": "text", "text": "Analyze this CCTV frame."}
    image_url = user["content"][1]["image_url"]["url"]
    encoded = base64.b64encode(b"frame-bytes").decode("ascii")
    assert image_url == f"data:image/jpeg;base64,{encoded}"

    assert seen["headers"]["authorization"] == "Bearer secret-key"
    assert seen["headers"]["content-type"] == "application/json"
    assert result == VisionResult("person", 1, 0, "Visitor at the gate", 0.9)


def test_omits_authorization_without_api_key() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        return _completion(VALID_RESULT)

    _vision(handler).analyze(b"frame", PROMPT)

    assert seen["authorization"] is None


def test_http_error_raises_vision_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    backend = _vision(handler, api_key="secret-key")

    with pytest.raises(VisionUnavailable, match="500") as exc_info:
        backend.analyze(b"frame", PROMPT)

    assert "secret-key" not in str(exc_info.value)
    assert backend.status()["error"] == "Vision endpoint returned HTTP 500"


def test_network_error_raises_vision_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    backend = _vision(handler)

    with pytest.raises(VisionUnavailable, match="ConnectError"):
        backend.analyze(b"frame", PROMPT)

    assert backend.status()["error"] is not None


def test_status_reports_configuration_without_calling_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("status must not call the endpoint")

    status = _vision(handler, api_key="secret-key").status()

    assert status == {
        "name": "openai",
        "model": "test-model",
        "base_url": "http://vision.local/v1",
        "available": True,
        "reasoning": "disabled",
        "error": None,
    }
    assert "secret-key" not in json.dumps(status)
