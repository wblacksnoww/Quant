"""加密凭据仓储。凭据永不回传明文，取明文必须显式 reveal 并审计。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...security.crypto import SecretBox, field_box
from ..models import Credential, utcnow_naive


class CredentialRepository:
    def __init__(self, session: Session, box: SecretBox | None = None) -> None:
        self.session = session
        self.box = box or field_box()

    def create(
        self,
        owner_id: int,
        name: str,
        kind: str,
        payload: dict[str, Any],
        description: str = "",
    ) -> Credential:
        record = Credential(
            owner_id=owner_id,
            name=name,
            kind=kind,
            description=description,
            payload=self.box.encrypt_json(payload, aad=f"credential:{owner_id}:{name}"),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def update(self, record: Credential, payload: dict[str, Any]) -> Credential:
        record.payload = self.box.encrypt_json(payload, aad=f"credential:{record.owner_id}:{record.name}")
        record.updated_at = utcnow_naive()
        return record

    def get(self, credential_id: int, *, owner_id: int | None = None) -> Credential | None:
        record = self.session.get(Credential, credential_id)
        if record is None:
            return None
        if owner_id is not None and record.owner_id != owner_id:
            return None
        return record

    def list_for(self, owner_id: int) -> list[Credential]:
        return list(self.session.scalars(select(Credential).where(Credential.owner_id == owner_id)))

    def reveal(self, record: Credential) -> dict[str, Any]:
        """解密并返回明文。调用方应写入审计日志。"""
        return self.box.decrypt_json(record.payload, aad=f"credential:{record.owner_id}:{record.name}")

    def delete(self, record: Credential) -> None:
        self.session.delete(record)
