"""
Pure-Python stdlib authenticated encryption for library PDF files.

Uses OpenSSL-backed SHAKE-256 counter-keystream encryption combined with
Encrypt-then-MAC (HMAC-SHA256) integrity verification and PBKDF2-HMAC-SHA256
master key derivation. Requires zero third-party packages.

Binary format:
    [8B MAGIC: b'LMENC01\\x00'][16B NONCE][CIPHERTEXT...][32B HMAC-SHA256 TAG]
"""

import hashlib
import hmac
import os
import tempfile
from pathlib import Path

from django.conf import settings

MAGIC = b"LMENC01\x00"
NONCE_SIZE = 16
TAG_SIZE = 32
HEADER_SIZE = len(MAGIC) + NONCE_SIZE
CHUNK_SIZE = 1024 * 1024  # 1 MiB streaming chunks


class EncryptionError(ValueError):
    """Raised when an encrypted PDF file is malformed or fails authentication."""


def _derive_master_key(secret: str | bytes | None = None) -> bytes:
    raw = secret if secret is not None else getattr(settings, "PDF_ENCRYPTION_SECRET", settings.SECRET_KEY)
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return hashlib.pbkdf2_hmac(
        "sha256",
        raw,
        b"lainamaakarit-library-salt-v1",
        iterations=10_000,
        dklen=32,
    )


def _derive_subkeys(master_key: bytes, nonce: bytes) -> tuple[bytes, bytes]:
    enc_key = hmac.digest(master_key, b"enc:" + nonce, "sha256")
    mac_key = hmac.digest(master_key, b"mac:" + nonce, "sha256")
    return enc_key, mac_key


def _xor_chunk(data: bytes, enc_key: bytes, nonce: bytes, chunk_index: int) -> bytes:
    if not data:
        return b""
    keystream = hashlib.shake_256(
        enc_key + nonce + chunk_index.to_bytes(8, "big")
    ).digest(len(data))
    return (int.from_bytes(data, "big") ^ int.from_bytes(keystream, "big")).to_bytes(
        len(data), "big"
    )


def encrypt_pdf_file(
    source_path: Path | str,
    dest_path: Path | str,
    secret: str | bytes | None = None,
) -> Path:
    """Encrypt a plaintext PDF file into dest_path using authenticated encryption."""
    src = Path(source_path)
    dst = Path(dest_path)
    dst.parent.mkdir(parents=True, exist_ok=True)

    master_key = _derive_master_key(secret)
    nonce = os.urandom(NONCE_SIZE)
    enc_key, mac_key = _derive_subkeys(master_key, nonce)
    mac = hmac.new(mac_key, MAGIC + nonce, "sha256")

    fd, tmp_name = tempfile.mkstemp(dir=dst.parent, prefix=".enc_tmp_")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out_f, src.open("rb") as in_f:
            out_f.write(MAGIC)
            out_f.write(nonce)
            chunk_idx = 0
            while True:
                chunk = in_f.read(CHUNK_SIZE)
                if not chunk:
                    break
                ciphertext = _xor_chunk(chunk, enc_key, nonce, chunk_idx)
                mac.update(ciphertext)
                out_f.write(ciphertext)
                chunk_idx += 1
            out_f.write(mac.digest())
        tmp_path.replace(dst)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return dst


def decrypt_pdf_file(
    encrypted_path: Path | str,
    dest_path: Path | str,
    secret: str | bytes | None = None,
) -> Path:
    """Verify authenticity and decrypt an encrypted PDF file to dest_path."""
    enc_file = Path(encrypted_path)
    dst = Path(dest_path)

    if not enc_file.is_file():
        raise EncryptionError(f"Salattua tiedostoa ei löytynyt: {enc_file}")

    total_size = enc_file.stat().st_size
    if total_size < HEADER_SIZE + TAG_SIZE:
        raise EncryptionError("Salattu tiedosto on liian lyhyt tai vioittunut.")

    ciphertext_len = total_size - HEADER_SIZE - TAG_SIZE
    master_key = _derive_master_key(secret)

    with enc_file.open("rb") as in_f:
        header = in_f.read(HEADER_SIZE)
        if header[: len(MAGIC)] != MAGIC:
            raise EncryptionError("Virheellinen salatun tiedoston tunniste.")
        nonce = header[len(MAGIC) :]
        enc_key, mac_key = _derive_subkeys(master_key, nonce)

        # Pass 1: Verify Encrypt-then-MAC before writing any plaintext output
        mac = hmac.new(mac_key, MAGIC + nonce, "sha256")
        remaining = ciphertext_len
        while remaining > 0:
            block = in_f.read(min(CHUNK_SIZE, remaining))
            if not block:
                raise EncryptionError("Odottamaton tiedoston loppu.")
            mac.update(block)
            remaining -= len(block)
        expected_tag = in_f.read(TAG_SIZE)
        if not hmac.compare_digest(mac.digest(), expected_tag):
            raise EncryptionError("Salatun PDF-tiedoston eheystarkistus epäonnistui.")

        # Pass 2: Stream decrypted PDF into temporary file and atomically move into place
        dst.parent.mkdir(parents=True, exist_ok=True)
        in_f.seek(HEADER_SIZE)
        fd, tmp_name = tempfile.mkstemp(dir=dst.parent, prefix=".dec_tmp_")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as out_f:
                remaining = ciphertext_len
                chunk_idx = 0
                while remaining > 0:
                    block = in_f.read(min(CHUNK_SIZE, remaining))
                    plaintext = _xor_chunk(block, enc_key, nonce, chunk_idx)
                    out_f.write(plaintext)
                    remaining -= len(block)
                    chunk_idx += 1
            tmp_path.replace(dst)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    return dst


def is_encrypted_file(path: Path | str) -> bool:
    """Return True if path begins with the Lainamääkärit encrypted header."""
    p = Path(path)
    if not p.is_file() or p.stat().st_size < HEADER_SIZE + TAG_SIZE:
        return False
    with p.open("rb") as f:
        return f.read(len(MAGIC)) == MAGIC
