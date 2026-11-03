import os

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from mu_dekhuchi.crypto import HikDecryptor

CODE = "abc123"


def _key(code: str) -> bytes:
    key = bytearray(16)
    raw = code.encode()[:16]
    key[: len(raw)] = raw
    return bytes(key)


def _encrypt_nal(plain: bytes, code: str = CODE) -> bytes:
    # The AES plaintext is the original NAL including its header; the clear
    # header is duplicated at the front. `count` is based on the original
    # NAL length, and the remainder stays in the clear.
    count = min((len(plain) // 16) * 16, 4096)
    cipher = Cipher(algorithms.AES(_key(code)), modes.ECB())
    encryptor = cipher.encryptor()
    encrypted = encryptor.update(plain[:count]) + encryptor.finalize()
    return plain[:1] + encrypted + plain[count:]


def test_decrypts_flagged_nal_with_verification_code() -> None:
    plain = bytes([0x65]) + os.urandom(64)
    encrypted = _encrypt_nal(plain)
    decryptor = HikDecryptor(CODE)

    assert decryptor.process(encrypted, True) == plain
    assert decryptor.decrypted == 1
    assert decryptor.saw_flag is True


def test_wrong_code_leaves_nal_untouched() -> None:
    plain = bytes([0x65]) + os.urandom(64)
    encrypted = _encrypt_nal(plain, "wrong")
    decryptor = HikDecryptor(CODE)

    assert decryptor.process(encrypted, True) == encrypted
    assert decryptor.decrypted == 0


def test_unflagged_nal_is_ignored_after_flag_seen() -> None:
    flagged = _encrypt_nal(bytes([0x41]) + os.urandom(32))
    plain = bytes([0x41]) + os.urandom(32)
    decryptor = HikDecryptor(CODE)

    decryptor.process(flagged, True)
    assert decryptor.process(plain, False) == plain


def test_disabled_without_code() -> None:
    nal = bytes([0x65]) + os.urandom(32)
    decryptor = HikDecryptor("")

    assert decryptor.enabled is False
    assert decryptor.process(nal, True) == nal
