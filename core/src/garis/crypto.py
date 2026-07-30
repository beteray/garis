"""Local encryption for memory and the credential vault.

Threat model, stated plainly: GARIS protects the user's memory and credentials
from anything reading files on the machine — backup tools, sync clients, other
user accounts, a stolen disk. It cannot protect against code already running as
the user with the master key available; nothing local can.

Key handling:
  * Windows — the key is wrapped with DPAPI (per-user, machine-bound), so the
    file on disk is useless on another machine or account.
  * POSIX — the key file is 0600 in a 0700 directory.
  * Either platform — a passphrase may wrap the key instead (scrypt), which is
    what "lock GARIS" means.
"""

from __future__ import annotations

import base64
import ctypes
import os
import secrets
import sys
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .errors import LockedError

KEY_SIZE = 32
NONCE_SIZE = 12
TOKEN_PREFIX = "g1"
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SALT_SIZE = 16


# --------------------------------------------------------------------------- #
# Windows DPAPI
# --------------------------------------------------------------------------- #

def dpapi_available() -> bool:
    return sys.platform == "win32"


def _data_blob_type() -> type[ctypes.Structure]:
    # ctypes.wintypes only imports on Windows, so the struct is built lazily.
    from ctypes import wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    return _DataBlob


def _dpapi(protect: bool, data: bytes) -> bytes:
    if not dpapi_available():
        raise LockedError("DPAPI jest dostępne tylko na Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    blob_cls = _data_blob_type()
    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = blob_cls(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = blob_cls()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    # CRYPTPROTECT_UI_FORBIDDEN — never block a background service on a prompt.
    args = (ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out))
    if not fn(*args):
        raise LockedError(
            "DPAPI odmówiło dostępu do klucza GARIS-a",
            user_message="Nie mogę odszyfrować swojej pamięci na tym koncie Windows.",
        )
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


# --------------------------------------------------------------------------- #
# Envelope encryption
# --------------------------------------------------------------------------- #

class SecretBox:
    """AES-256-GCM over a 32-byte key. Tokens are ``g1.<nonce>.<ciphertext>``."""

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_SIZE:
            raise ValueError(f"Klucz musi mieć {KEY_SIZE} bajtów, ma {len(key)}")
        self._aead = AESGCM(key)

    def encrypt(self, plaintext: str | bytes, *, context: str = "") -> str:
        raw = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
        nonce = secrets.token_bytes(NONCE_SIZE)
        ct = self._aead.encrypt(nonce, raw, context.encode("utf-8") or None)
        return f"{TOKEN_PREFIX}.{_b64e(nonce)}.{_b64e(ct)}"

    def decrypt(self, token: str, *, context: str = "") -> bytes:
        try:
            prefix, nonce_b64, ct_b64 = token.split(".", 2)
        except ValueError as exc:
            raise LockedError(f"Uszkodzony token: {token[:16]}…") from exc
        if prefix != TOKEN_PREFIX:
            raise LockedError(f"Nieznana wersja szyfrowania: {prefix!r}")
        try:
            return self._aead.decrypt(
                _b64d(nonce_b64), _b64d(ct_b64), context.encode("utf-8") or None
            )
        except (InvalidTag, ValueError) as exc:
            raise LockedError(
                "Nie udało się odszyfrować danych — inny klucz lub naruszony zapis",
                user_message="Nie mogę odczytać zaszyfrowanych danych. Klucz się nie zgadza.",
            ) from exc

    def decrypt_text(self, token: str, *, context: str = "") -> str:
        return self.decrypt(token, context=context).decode("utf-8")


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


# --------------------------------------------------------------------------- #
# Master key lifecycle
# --------------------------------------------------------------------------- #

_PLAIN_MARK = b"garis-key-v1:"
_DPAPI_MARK = b"garis-key-dpapi-v1:"
_SCRYPT_MARK = b"garis-key-scrypt-v1:"


def derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = Scrypt(salt=salt, length=KEY_SIZE, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return kdf.derive(passphrase.encode("utf-8"))


def load_or_create_master_key(
    key_file: Path, *, passphrase: str | None = None, use_dpapi: bool | None = None
) -> bytes:
    """Return the instance's master key, creating it on first run.

    ``use_dpapi`` defaults to True on Windows. Passing a passphrase switches to
    scrypt wrapping on any platform and takes precedence.
    """
    if use_dpapi is None:
        use_dpapi = dpapi_available()

    if key_file.exists():
        return _unwrap(key_file.read_bytes(), passphrase=passphrase)

    key = secrets.token_bytes(KEY_SIZE)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    blob = _wrap(key, passphrase=passphrase, use_dpapi=use_dpapi)
    tmp = key_file.with_suffix(".tmp")
    tmp.write_bytes(blob)
    if sys.platform != "win32":
        os.chmod(tmp, 0o600)
    os.replace(tmp, key_file)
    return key


def _wrap(key: bytes, *, passphrase: str | None, use_dpapi: bool) -> bytes:
    if passphrase:
        salt = secrets.token_bytes(SALT_SIZE)
        box = SecretBox(derive_key(passphrase, salt))
        return _SCRYPT_MARK + base64.b64encode(salt) + b"." + box.encrypt(key).encode("ascii")
    if use_dpapi:
        return _DPAPI_MARK + base64.b64encode(_dpapi(True, key))
    return _PLAIN_MARK + base64.b64encode(key)


def _unwrap(blob: bytes, *, passphrase: str | None) -> bytes:
    if blob.startswith(_SCRYPT_MARK):
        if not passphrase:
            raise LockedError(
                "Klucz jest chroniony hasłem",
                user_message="Potrzebuję hasła, żeby odblokować swoją pamięć.",
            )
        body = blob[len(_SCRYPT_MARK):]
        salt_b64, token = body.split(b".", 1)
        box = SecretBox(derive_key(passphrase, base64.b64decode(salt_b64)))
        return box.decrypt(token.decode("ascii"))
    if blob.startswith(_DPAPI_MARK):
        return _dpapi(False, base64.b64decode(blob[len(_DPAPI_MARK):]))
    if blob.startswith(_PLAIN_MARK):
        return base64.b64decode(blob[len(_PLAIN_MARK):])
    raise LockedError("Nierozpoznany format pliku klucza")


def subkey(master: bytes, label: str) -> bytes:
    """Domain-separated key so the vault and memory never share one.

    Deriving instead of storing two keys keeps a single unlock path while
    ensuring a leaked memory key cannot open the vault.
    """
    kdf = Scrypt(salt=label.encode("utf-8").ljust(SALT_SIZE, b"\0")[:SALT_SIZE],
                 length=KEY_SIZE, n=2**14, r=SCRYPT_R, p=SCRYPT_P)
    return kdf.derive(master)


__all__ = [
    "KEY_SIZE",
    "SecretBox",
    "derive_key",
    "dpapi_available",
    "load_or_create_master_key",
    "subkey",
]
