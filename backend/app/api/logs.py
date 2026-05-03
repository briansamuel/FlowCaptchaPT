"""Usage logs endpoint."""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import UsageLog
from .auth import require_admin

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("")
async def get_logs(
    _=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    action: Optional[str] = None,
    success: Optional[bool] = None,
):
    q = select(UsageLog).order_by(UsageLog.created_at.desc())

    if action:
        q = q.where(UsageLog.action == action)
    if success is not None:
        q = q.where(UsageLog.success == success)

    total = await db.scalar(
        select(func.count(UsageLog.id)).select_from(q.subquery())
    )

    q = q.offset((page - 1) * limit).limit(limit)
    result = await db.execute(q)
    logs = result.scalars().all()

    return {
        "total": total or 0,
        "page": page,
        "limit": limit,
        "items": [
            {
                "id": l.id,
                "api_key_id": l.api_key_id,
                "action": l.action,
                "success": l.success,
                "error": l.error,
                "token_preview": l.token_preview,
                "ip_address": l.ip_address,
                "response_time_ms": l.response_time_ms,
                "callback_result": l.callback_result,
                "created_at": l.created_at.isoformat() if l.created_at else None,
            }
            for l in logs
        ],
    }
