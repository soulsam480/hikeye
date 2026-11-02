from netra.rtsp import (
    NalAssembler,
    RtpPacket,
    build_authorization,
    parse_challenge,
    parse_rtp,
    parse_sdp,
    resolve_control,
)


def _rtp(payload: bytes, *, flagged: bool = False, marker: bool = False) -> bytes:
    first = 0x80 | (0x10 if flagged else 0)
    second = 0x80 if marker else 0
    header = bytes([first, second, 0, 1]) + (123).to_bytes(4, "big") + b"\x00\x00\x00\x01"
    extension = b"\x40\x00\x00\x00" if flagged else b""
    return header + extension + payload


def test_parse_challenge_and_rfc_digest_vector() -> None:
    challenge = parse_challenge(
        'Digest realm="testrealm@host.com", nonce="dcd98b7102dd2f0e8b11d0f600bfb0c093", qop="auth"'
    )
    assert challenge is not None

    header = build_authorization(
        challenge,
        "GET",
        "/dir/index.html",
        "Mufasa",
        "Circle Of Life",
        {"nc": 0},
        cnonce="0a4f113b",
    )
    assert 'response="6629fae49393a05397450978507c4ef1"' in header
    assert "qop=auth" in header
    assert "nc=00000001" in header


def test_parse_sdp_extracts_h264_parameters() -> None:
    sdp = """v=0
m=video 0 RTP/AVP 96
a=rtpmap:96 H264/90000
a=fmtp:96 packetization-mode=0;sprop-parameter-sets=Z2QAC6wbGoFgloQAAC7gAAV+QBA=,aO48sA==
a=control:*
"""
    info = parse_sdp(sdp)

    assert info is not None
    assert info.codec == "h264"
    assert info.control == "*"
    assert info.sps is not None and info.sps[0] == 0x67
    assert info.pps is not None and info.pps[0] == 0x68
    assert resolve_control("rtsp://host/Streaming/Channels/102", "*") == (
        "rtsp://host/Streaming/Channels/102"
    )


def test_parse_rtp_reads_hikvision_extension_flag() -> None:
    packet = parse_rtp(_rtp(b"\x65abc", flagged=True, marker=True))

    assert packet is not None
    assert packet.payload == b"\x65abc"
    assert packet.flagged is True
    assert packet.marker is True
    assert packet.timestamp == 123


def test_assembler_handles_single_nal_and_fua() -> None:
    assembler = NalAssembler()
    single = parse_rtp(_rtp(b"\x65hello"))
    assert single is not None
    assert assembler.feed(single) == [(b"\x65hello", 123, False)]

    start = parse_rtp(_rtp(bytes([0x7C, 0x85]) + b"hello", flagged=True))
    end = parse_rtp(_rtp(bytes([0x7C, 0x45]) + b"world"))
    assert start is not None and end is not None
    assert assembler.feed(start) == []
    assert assembler.feed(end) == [(b"\x65helloworld", 123, True)]


def test_assembler_handles_stap_a() -> None:
    assembler = NalAssembler()
    first, second = b"\x67aaa", b"\x68bbb"
    payload = (
        bytes([24])
        + len(first).to_bytes(2, "big")
        + first
        + len(second).to_bytes(2, "big")
        + second
    )
    packet = parse_rtp(_rtp(payload))
    assert packet is not None

    units = assembler.feed(packet)
    assert [unit[0] for unit in units] == [first, second]


def test_rtp_requires_valid_version() -> None:
    assert parse_rtp(b"\x00" + b"\x00" * 11) is None
    assert parse_rtp(RtpPacket.__new__(RtpPacket).__class__ and b"") is None
