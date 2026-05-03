"""Auth helpers - Admin token + API key validation."""
import hashlib
from datetime import datetime
from typing import Optional

from fastapi import Header, HTTPException, Depends, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models import ApiKey, UsageLog


async def require_admin(
    authorization: Optional[str] = Header(None),
):
    """Validate admin Bearer token."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid auth format")
    if parts[1] != settings.admin_token:
        raise HTTPException(status_code=403, detail="Invalid admin token")
    return True


async def validate_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-Api-Key"),
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> ApiKey:
    """Extract and validate API key from headers."""
    raw_key = None
    if x_api_key:
        raw_key = x_api_key
    elif authorization:
        parts = authorization.split(" ", 1)
        if len(parts) == 2 and parts[1].startswith("fc_"):
            raw_key = parts[1]

    if not raw_key:
        raise HTTPException(
            status_code=401,
            detail="API key required. Use X-Api-Key header.",
        )

    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    result = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
    api_key = result.scalar_one_or_none()

    if not api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")
    if not api_key.is_active:
        raise HTTPException(status_code=403, detail="API key is disabled")
    if api_key.expires_at and api_key.expires_at < datetime.utcnow():
        raise HTTPException(status_code=403, detail="API key expired")

    # Rate limit check (requests per minute)
    from datetime import timedelta
    one_min_ago = datetime.utcnow() - timedelta(minutes=1)
    count = await db.scalar(
        select(func.count(UsageLog.id)).where(
            UsageLog.api_key_id == api_key.id,
            UsageLog.created_at >= one_min_ago,
        )
    )
    if count and count >= api_key.rate_limit:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded ({api_key.rate_limit}/min)",
        )

    return api_key
