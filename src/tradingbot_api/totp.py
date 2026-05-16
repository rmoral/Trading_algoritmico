"""TOTP second factor + symmetric encryption of the stored secret.

The TOTP secret never lives in the database in plaintext. We encrypt
it with Fernet (AES-128 in CBC + HMAC-SHA256) using a key derived
from `WEB_API_SECRET_KEY`. Rotating that secret would invalidate all
enrolled secrets — operator's call.

Helpers:
- `new_totp_secret`: 32-character base32 string from `pyotp`.
- `provisioning_uri`: otpauth:// URL for Google Authenticator / 1Password.
- `verify_totp`: validates a 6-digit code with a +/- 30-second window.
- `encrypt_totp_secret` / `decrypt_totp_secret`: Fernet round-trip.
"""

from __future__ import annotations

import base64
import hashlib

import pyotp
from cryptography.fernet import Fernet, InvalidToken

ISSUER_NAME = "TradingBot"
TOTP_WINDOW = 1  # +/- 30s tolerance for clock drift


def new_totp_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, *, account_name: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(
        name=account_name, issuer_name=ISSUER_NAME
    )


def verify_totp(secret: str, code: str) -> bool:
    return bool(pyotp.TOTP(secret).verify(code, valid_window=TOTP_WINDOW))


def _derive_fernet_key(secret_key: str) -> bytes:
    """Derive a Fernet key from `WEB_API_SECRET_KEY`.

    Fernet expects 32 bytes urlsafe-base64-encoded. We SHA-256 the
    server secret to a 32-byte digest and encode. Deterministic, so
    encrypted secrets round-trip across restarts as long as the
    secret key is stable.
    """
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_totp_secret(plaintext: str, *, server_secret: str) -> str:
    f = Fernet(_derive_fernet_key(server_secret))
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_totp_secret(ciphertext: str, *, server_secret: str) -> str:
    f = Fernet(_derive_fernet_key(server_secret))
    try:
        return f.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("invalid or tampered ciphertext") from exc
