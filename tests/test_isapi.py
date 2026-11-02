from collections.abc import Callable

import httpx
import pytest

from netra.isapi import IsapiClient

EZVIZ_XML = """<?xml version="1.0" encoding="UTF-8"?>
<EZVIZ version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
<verificationCode>ABC123</verificationCode>
<streamEncrypteEnabled>true</streamEncrypteEnabled>
</EZVIZ>
"""

CHANNEL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<StreamingChannel version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
<id>102</id>
<channelName>Driveway</channelName>
<video>
<videoCodecType>H.265</videoCodecType>
<videoResolutionWidth>1920</videoResolutionWidth>
<videoResolutionHeight>1080</videoResolutionHeight>
</video>
</StreamingChannel>
"""

CHANNEL_XML_H264 = CHANNEL_XML.replace(
    "<videoCodecType>H.265</videoCodecType>",
    "<videoCodecType>H.264</videoCodecType>",
)


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> IsapiClient:
    return IsapiClient(
        "http://camera.local",
        "admin",
        "s3cret",
        transport=httpx.MockTransport(handler),
    )


def test_read_verification_code_parses_value() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/ISAPI/System/Network/EZVIZ"
        assert request.url.userinfo == b""
        assert "s3cret" not in str(request.url)
        return httpx.Response(200, text=EZVIZ_XML)

    with _client(handler) as client:
        assert client.read_verification_code() == "ABC123"


def test_read_verification_code_returns_none_when_absent() -> None:
    xml = """<EZVIZ xmlns="http://www.hikvision.com/ver20/XMLSchema">
<other>value</other>
</EZVIZ>"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=xml)

    with _client(handler) as client:
        assert client.read_verification_code() is None


def test_read_verification_code_returns_none_for_blank_value() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<EZVIZ><verificationCode>  </verificationCode></EZVIZ>")

    with _client(handler) as client:
        assert client.read_verification_code() is None


@pytest.mark.parametrize(("value", "expected"), [("true", True), ("false", False)])
def test_is_stream_encryption_enabled_reads_flag(value: str, expected: bool) -> None:
    xml = f"<EZVIZ><streamEncrypteEnabled>{value}</streamEncrypteEnabled></EZVIZ>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=xml)

    with _client(handler) as client:
        assert client.is_stream_encryption_enabled() is expected


def test_is_stream_encryption_enabled_returns_none_when_absent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<EZVIZ><verificationCode>ABC</verificationCode></EZVIZ>")

    with _client(handler) as client:
        assert client.is_stream_encryption_enabled() is None


@pytest.mark.parametrize("stream_id", [102, "102"])
def test_get_channel_returns_raw_xml(stream_id: int | str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/ISAPI/Streaming/channels/102"
        return httpx.Response(200, text=CHANNEL_XML)

    with _client(handler) as client:
        assert client.get_channel(stream_id) == CHANNEL_XML


def test_get_snapshot_returns_jpeg_bytes() -> None:
    payload = b"\xff\xd8\xff\xe0jpeg"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/ISAPI/Streaming/channels/102/picture"
        return httpx.Response(200, content=payload, headers={"content-type": "image/jpeg"})

    with _client(handler) as client:
        assert client.get_snapshot(102) == payload


def test_get_snapshot_rejects_non_jpeg() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>error</html>")

    with _client(handler) as client, pytest.raises(RuntimeError, match="JPEG"):
        client.get_snapshot(102)


def test_set_channel_codec_put_preserves_other_fields() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/ISAPI/Streaming/channels/102"
        if request.method == "GET":
            return httpx.Response(200, text=CHANNEL_XML)

        assert request.method == "PUT"
        assert request.headers["content-type"].startswith("application/xml")
        seen["body"] = request.content.decode()
        return httpx.Response(200, text="<ResponseStatus/>")

    with _client(handler) as client:
        assert client.set_channel_codec(102) is True

    body = seen["body"]
    assert "<videoCodecType>H.264</videoCodecType>" in body
    assert "H.265" not in body
    assert "<id>102</id>" in body
    assert "<channelName>Driveway</channelName>" in body
    assert "<videoResolutionWidth>1920</videoResolutionWidth>" in body
    assert "<videoResolutionHeight>1080</videoResolutionHeight>" in body
    assert body == CHANNEL_XML_H264


def test_set_channel_codec_rejects_missing_codec_element() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<StreamingChannel><id>102</id></StreamingChannel>")

    with _client(handler) as client, pytest.raises(RuntimeError, match="videoCodecType"):
        client.set_channel_codec(102)


def test_non_2xx_raises_runtime_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    with _client(handler) as client, pytest.raises(RuntimeError, match="HTTP 500"):
        client.get_channel(102)
