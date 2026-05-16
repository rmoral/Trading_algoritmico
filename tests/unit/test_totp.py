"""Unit tests for TOTP helpers + secret encryption."""

from __future__ import annotations

import pyotp
import pytest

from tradingbot_api.totp import (
    ISSUER_NAME,
    decrypt_totp_secret,
    encrypt_totp_secret,
    new_totp_secret,
    provisioning_uri,
    verify_totp,
)

SERVER_SECRET = "test-server-secret-32-bytes-long-xyz"


def test_new_secret_is_valid_base32() -> None:
    secret = new_totp_secret()
    # pyotp can parse it and produce a code.
    code = pyotp.TOTP(secret).now()
    assert len(code) == 6
    assert code.isdigit()


def test_verify_accepts_current_code() -> None:
    secret = new_totp_secret()
    code = pyotp.TOTP(secret).now()
    assert verify_totp(secret, code) is True


def test_verify_rejects_random_code() -> None:
    secret = new_totp_secret()
    assert verify_totp(secret, "000000") is False or verify_totp(secret, "999999") is False


def test_provisioning_uri_contains_issuer_and_account() -> None:
    uri = provisioning_uri("ABCDEFGHIJKLMNOP", account_name="alice")
    assert uri.startswith("otpauth://totp/")
    assert ISSUER_NAME in uri
    assert "alice" in uri


def test_encryption_round_trip() -> None:
    plaintext = new_totp_secret()
    ciphertext = encrypt_totp_secret(plaintext, server_secret=SERVER_SECRET)
    assert ciphertext != plaintext
    assert decrypt_totp_secret(ciphertext, server_secret=SERVER_SECRET) == plaintext


def test_decryption_rejects_tampered_ciphertext() -> None:
    plaintext = new_totp_secret()
    ciphertext = encrypt_totp_secret(plaintext, server_secret=SERVER_SECRET)
    tampered = ciphertext[:-2] + "AA"
    with pytest.raises(ValueError):
        decrypt_totp_secret(tampered, server_secret=SERVER_SECRET)


def test_decryption_rejects_wrong_key() -> None:
    plaintext = new_totp_secret()
    ciphertext = encrypt_totp_secret(plaintext, server_secret=SERVER_SECRET)
    with pytest.raises(ValueError):
        decrypt_totp_secret(ciphertext, server_secret="different-secret")


def test_ciphertext_is_different_each_call() -> None:
    """Fernet bundles a fresh IV so the ciphertext is not deterministic."""
    plaintext = "JBSWY3DPEHPK3PXP"
    a = encrypt_totp_secret(plaintext, server_secret=SERVER_SECRET)
    b = encrypt_totp_secret(plaintext, server_secret=SERVER_SECRET)
    assert a != b
    assert decrypt_totp_secret(a, server_secret=SERVER_SECRET) == plaintext
    assert decrypt_totp_secret(b, server_secret=SERVER_SECRET) == plaintext
