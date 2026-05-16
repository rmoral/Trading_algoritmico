"""Unit tests for password hashing helpers."""

from __future__ import annotations

from tradingbot_api.auth import hash_password, verify_password


def test_hash_is_argon2id() -> None:
    h = hash_password("hunter2")
    assert h.startswith("$argon2id$")


def test_hash_is_salted_so_each_call_is_unique() -> None:
    a = hash_password("hunter2")
    b = hash_password("hunter2")
    assert a != b


def test_verify_accepts_correct_password() -> None:
    h = hash_password("hunter2")
    assert verify_password("hunter2", h) is True


def test_verify_rejects_wrong_password() -> None:
    h = hash_password("hunter2")
    assert verify_password("hunter3", h) is False


def test_verify_rejects_garbage_hash() -> None:
    assert verify_password("hunter2", "not-an-argon2-hash") is False
