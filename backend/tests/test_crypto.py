"""安全组件单测：字段加密、口令、JWT、TOTP。"""

from __future__ import annotations

import secrets

import pytest

from quant.core.errors import AuthError, CryptoError, MasterKeyMissing
from quant.security.crypto import INFO_FIELD, MasterKey, SecretBox
from quant.security.password import (
    WeakPassword,
    hash_password,
    validate_strength,
    verify_password,
)
from quant.security.tokens import TokenService
from quant.security.totp import TotpService


def new_master() -> MasterKey:
    return MasterKey(secrets.token_bytes(32), "test")


def test_field_cipher_roundtrip_and_aad_binding():
    box = SecretBox.from_master(new_master(), INFO_FIELD, name="field")
    token = box.encrypt("sk-live-secret", aad="credential:1:api_key")
    assert token.startswith("v1.")
    assert box.decrypt(token, aad="credential:1:api_key") == "sk-live-secret"

    # 把密文挪到别的字段/别的记录上必须失败
    with pytest.raises(CryptoError):
        box.decrypt(token, aad="credential:2:api_key")
    with pytest.raises(CryptoError):
        box.decrypt(token, aad="credential:1:api_secret")


def test_field_cipher_tamper_detection():
    box = SecretBox.from_master(new_master(), INFO_FIELD, name="field")
    token = box.encrypt("hello", aad="x")
    body = list(token)
    body[-6] = "A" if body[-6] != "A" else "B"
    with pytest.raises(CryptoError):
        box.decrypt("".join(body), aad="x")


def test_master_key_rejects_bad_length():
    with pytest.raises(MasterKeyMissing):
        MasterKey(b"short", "test")
    with pytest.raises(MasterKeyMissing):
        MasterKey(b"\x00" * 32, "test")


def test_password_hash_and_strength():
    stored = hash_password("Str0ng!Passw0rd")
    assert stored.startswith("$argon2")
    assert verify_password("Str0ng!Passw0rd", stored)
    assert not verify_password("wrong-password", stored)
    assert not verify_password("", stored)

    with pytest.raises(WeakPassword):
        validate_strength("short")
    with pytest.raises(WeakPassword):
        validate_strength("alllowercaseonly")
    validate_strength("Str0ng!Passw0rd")


def test_jwt_type_confusion_is_blocked():
    service = TokenService(new_master(), access_ttl=60, refresh_ttl=3600)
    pair = service.issue_pair(7, "admin")

    claims = service.decode(pair.access_token, expected_type="access")
    assert claims.user_id == 7 and claims.role == "admin"

    with pytest.raises(AuthError):
        service.decode(pair.access_token, expected_type="refresh")
    with pytest.raises(AuthError):
        service.decode(pair.refresh_token, expected_type="access")
    with pytest.raises(AuthError):
        service.decode(pair.access_token[:-2] + "xx", expected_type="access")


def test_totp_secret_encryption_and_verification():
    box = SecretBox.from_master(new_master(), INFO_FIELD, name="field")
    service = TotpService(box, issuer="QuantTest")
    secret = service.generate_secret()

    encrypted = service.encrypt_secret(secret, user_id=42)
    assert secret not in encrypted
    assert service.decrypt_secret(encrypted, user_id=42) == secret
    with pytest.raises(CryptoError):
        service.decrypt_secret(encrypted, user_id=43)

    assert service.verify(secret, service.current_code(secret))
    assert not service.verify(secret, "000000")

    codes = service.generate_recovery_codes(3)
    hashed = [service.hash_recovery_code(code) for code in codes]
    assert service.verify_recovery_code(codes[1], hashed)
    assert not service.verify_recovery_code("AAAA-BBBB", hashed)
