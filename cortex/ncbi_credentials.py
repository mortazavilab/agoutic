"""Encrypted per-user credentials used for NCBI literature requests."""
from __future__ import annotations

import os
import uuid

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from cortex.db import SessionLocal
from cortex.models import UserNCBICredential


def _fernet() -> Fernet:
    key = os.getenv("NCBI_CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "NCBI_CREDENTIAL_ENCRYPTION_KEY must be configured before saving an NCBI API key."
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError("NCBI_CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key.") from exc


def credential_status(credential: UserNCBICredential | None) -> dict[str, object]:
    return {
        "email": credential.email if credential else None,
        "has_api_key": bool(credential and credential.api_key_ciphertext),
    }


def get_credential(user_id: str) -> UserNCBICredential | None:
    session = SessionLocal()
    try:
        return session.execute(
            select(UserNCBICredential).where(UserNCBICredential.user_id == user_id)
        ).scalar_one_or_none()
    finally:
        session.close()


def load_ncbi_credentials(user_id: str):
    """Return decrypted credentials for an outbound request only."""
    from literature.service import NCBICredentials

    credential = get_credential(user_id)
    if credential is None:
        return NCBICredentials()
    api_key = None
    if credential.api_key_ciphertext:
        try:
            api_key = _fernet().decrypt(credential.api_key_ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise RuntimeError("Stored NCBI API key could not be decrypted.") from exc
    return NCBICredentials(email=credential.email, api_key=api_key)


def save_credential(user_id: str, email: str | None, api_key: str | None) -> UserNCBICredential:
    session = SessionLocal()
    try:
        credential = session.execute(
            select(UserNCBICredential).where(UserNCBICredential.user_id == user_id)
        ).scalar_one_or_none()
        if credential is None:
            credential = UserNCBICredential(id=str(uuid.uuid4()), user_id=user_id)
            session.add(credential)
        credential.email = email
        if api_key is not None:
            credential.api_key_ciphertext = _fernet().encrypt(api_key.encode("utf-8")).decode("ascii")
        session.commit()
        session.refresh(credential)
        return credential
    finally:
        session.close()


def delete_api_key(user_id: str) -> bool:
    session = SessionLocal()
    try:
        credential = session.execute(
            select(UserNCBICredential).where(UserNCBICredential.user_id == user_id)
        ).scalar_one_or_none()
        if credential is None or not credential.api_key_ciphertext:
            return False
        credential.api_key_ciphertext = None
        session.commit()
        return True
    finally:
        session.close()
