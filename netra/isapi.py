from __future__ import annotations

import re
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import httpx

EZVIZ_PATH = "/ISAPI/System/Network/EZVIZ"
CHANNEL_PATH = "/ISAPI/Streaming/channels/{stream_id}"
CODEC_ELEMENT = "videoCodecType"

_CODEC_PATTERN = re.compile(
    r"<videoCodecType\b[^>]*/>|<videoCodecType\b[^>]*>.*?</videoCodecType>",
    re.DOTALL,
)


class IsapiClient:
    """Minimal Hikvision ISAPI client using HTTP Digest auth."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout: float = 8.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            auth=httpx.DigestAuth(username, password),
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> IsapiClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def read_verification_code(self) -> str | None:
        return _clean(_xml_text(self._get(EZVIZ_PATH), "verificationCode"))

    def is_stream_encryption_enabled(self) -> bool | None:
        value = _clean(_xml_text(self._get(EZVIZ_PATH), "streamEncrypteEnabled"))
        if value is None:
            return None
        return value.lower() in {"true", "1"}

    def get_channel(self, stream_id: int | str) -> str:
        return self._get(_channel_path(stream_id))

    def set_channel_codec(self, stream_id: int | str, codec: str = "H.264") -> bool:
        """PUT the channel back with only <videoCodecType> changed."""
        path = _channel_path(stream_id)
        updated = _replace_codec(self._get(path), codec)
        self._request("PUT", path, updated)
        return True

    def _get(self, path: str) -> str:
        return self._request("GET", path)

    def _request(self, method: str, path: str, body: str | None = None) -> str:
        headers = {"Content-Type": "application/xml"} if body is not None else None
        response = self._client.request(method, path, content=body, headers=headers)
        if not response.is_success:
            raise RuntimeError(
                f"ISAPI {method} {path} failed: HTTP {response.status_code} "
                f"{response.reason_phrase}"
            )
        return response.text


def read_verification_code(
    base_url: str,
    username: str,
    password: str,
    timeout: float = 8.0,
) -> str | None:
    with IsapiClient(base_url, username, password, timeout) as client:
        return client.read_verification_code()


def is_stream_encryption_enabled(
    base_url: str,
    username: str,
    password: str,
    timeout: float = 8.0,
) -> bool | None:
    with IsapiClient(base_url, username, password, timeout) as client:
        return client.is_stream_encryption_enabled()


def get_channel(
    base_url: str,
    username: str,
    password: str,
    stream_id: int | str,
    timeout: float = 8.0,
) -> str:
    with IsapiClient(base_url, username, password, timeout) as client:
        return client.get_channel(stream_id)


def set_channel_codec(
    base_url: str,
    username: str,
    password: str,
    stream_id: int | str,
    codec: str = "H.264",
    timeout: float = 8.0,
) -> bool:
    with IsapiClient(base_url, username, password, timeout) as client:
        return client.set_channel_codec(stream_id, codec)


def _channel_path(stream_id: int | str) -> str:
    return CHANNEL_PATH.format(stream_id=stream_id)


def _xml_text(xml: str, tag: str) -> str | None:
    """Return an element's text, matching on the local name so namespaces do not matter."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return None

    for element in root.iter():
        if isinstance(element.tag, str) and element.tag.rpartition("}")[2] == tag:
            return element.text
    return None


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    value = text.strip()
    return value or None


def _replace_codec(xml: str, codec: str) -> str:
    if not _CODEC_PATTERN.search(xml):
        raise RuntimeError(f"Channel XML has no <{CODEC_ELEMENT}> element")
    replacement = f"<{CODEC_ELEMENT}>{escape(codec)}</{CODEC_ELEMENT}>"
    return _CODEC_PATTERN.sub(replacement, xml, count=1)
