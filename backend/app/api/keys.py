"""API Key management endpoints."""
import hashlib
import secrets
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import ApiKey, UsageLog
from .auth import require_admin

router = APIRouter(prefix="/api/keys", tags=["api-keys"])


def _generate_key() -> tuple[str, str, str]:
    """Generate API key -> (raw_key, hash, prefix)."""
    raw = "fc_" + secrets.token_urlsafe(32)
    h = hashlib.sha256(raw.encode()).hexdigest()
    prefix = raw[:12]
    return raw, h, prefix


class CreateKeyRequest(BaseModel):
    name: str
    rate_limit: int = 60
    allowed_actions: str = "ALL"  # ALL, VIDEO, IMAGE


class CreateKeyResponse(BaseModel):
    id: str
    name: str
    key: str  # Only shown once
    key_prefix: str
    rate_limit: int
    allowed_actions: str


class KeyInfo(BaseModel):
    id: str
    name: str
    key_prefix: str
    is_active: bool
    rate_limit: int
    allowed_actions: str
    created_at: datetime
    total_requests: int = 0
    success_count: int = 0


@router.post("", response_model=CreateKeyResponse)
async def create_key(
    req: CreateKeyRequest,
    _=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    raw, h, prefix = _generate_key()
    key = ApiKey(
        name=req.name,
        key_hash=h,
        key_prefix=prefix,
        rate_limit=req.rate_limit,
        allowed_actions=req.allowed_actions,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return CreateKeyResponse(
        id=key.id, name=key.name, key=raw,
        key_prefix=prefix, rate_limit=key.rate_limit,
        allowed_actions=key.allowed_actions,
    )


@router.get("", response_model=list[KeyInfo])
async def list_keys(
    _=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
    keys = result.scalars().all()

    infos = []
    for k in keys:
        total = await db.scalar(
            select(func.count(UsageLog.id)).where(UsageLog.api_key_id == k.id)
        )
        success = await db.scalar(
            select(func.count(UsageLog.id)).where(
                UsageLog.api_key_id == k.id, UsageLog.success == True
            )
        )
        infos.append(KeyInfo(
            id=k.id, name=k.name, key_prefix=k.key_prefix,
            is_active=k.is_active, rate_limit=k.rate_limit,
            allowed_actions=k.allowed_actions, created_at=k.created_at,
            total_requests=total or 0, success_count=success or 0,
        ))
    return infos


@router.delete("/{key_id}")
async def delete_key(
    key_id: str,
    _=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    key = await db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")
    await db.delete(key)
    await db.commit()
    return {"ok": True}


@router.put("/{key_id}/toggle")
async def toggle_key(
    key_id: str,
    _=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    key = await db.get(ApiKey, key_id)
    if not key:
        raise HTTPException(status_code=404, detail="Key not found")
    key.is_active = not key.is_active
    await db.commit()
    return {"id": key.id, "is_active": key.is_active}
