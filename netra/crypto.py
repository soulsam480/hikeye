from __future__ import annotations

import hashlib

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

MAX_ENCRYPTED = 4096


class HikDecryptor:
    """Hikvision encrypted-stream NAL decryptor.

    Layout: ``[NAL header] + AES-128-ECB(original NAL, first 4 KB) + plaintext``.
    The key is the verification code zero-padded to 16 bytes. Decrypting the
    first block must reproduce the clear NAL header; that sanity check prevents
    touching unencrypted streams.
    """

    def __init__(self, verification_code: str, header_size: int = 1) -> None:
        self.key = bytearray(16)
        if verification_code:
            raw = verification_code.encode("utf-8")[:16]
            self.key[: len(raw)] = raw
        self.key = bytes(self.key)
        self.enabled = bool(verification_code)
        self.saw_flag = False
        self.decrypted = 0
        self.header_size = header_size

    def _decrypt(self, block: bytes) -> bytes:
        cipher = Cipher(algorithms.AES(self.key), modes.ECB())
        decryptor = cipher.decryptor()
        return decryptor.update(block) + decryptor.finalize()

    def process(self, nal: bytes, flagged: bool) -> bytes:
        if not self.enabled or len(nal) < 16 + self.header_size:
            return nal
        if flagged:
            self.saw_flag = True
        if self.saw_flag and not flagged:
            return nal

        body = nal[self.header_size :]
        first = self._decrypt(body[:16])
        if first[: self.header_size] != nal[: self.header_size]:
            return nal

        count = min((len(body) // 16) * 16, MAX_ENCRYPTED)
        tail = self._decrypt(body[16:count]) if count > 16 else b""
        self.decrypted += 1
        return first + tail + body[count:]

    @staticmethod
    def fingerprint(code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()[:8]
