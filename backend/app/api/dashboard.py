"""Dashboard stats endpoint."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import ApiKey, UsageLog
from ..captcha.queue import job_queue
from .auth import require_admin

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/stats")
async def get_stats(
    _=Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    last_hour = now - timedelta(hours=1)

    total_keys = await db.scalar(select(func.count(ApiKey.id)))
    active_keys = await db.scalar(
        select(func.count(ApiKey.id)).where(ApiKey.is_active == True)
    )
    total_requests = await db.scalar(select(func.count(UsageLog.id)))
    today_requests = await db.scalar(
        select(func.count(UsageLog.id)).where(UsageLog.created_at >= today)
    )
    hour_requests = await db.scalar(
        select(func.count(UsageLog.id)).where(UsageLog.created_at >= last_hour)
    )
    success_count = await db.scalar(
        select(func.count(UsageLog.id)).where(UsageLog.success == True)
    )
    avg_time = await db.scalar(
        select(func.avg(UsageLog.response_time_ms)).where(
            UsageLog.success == True
        )
    )

    rate = (success_count / total_requests * 100) if total_requests else 0

    return {
        "total_keys": total_keys or 0,
        "active_keys": active_keys or 0,
        "total_requests": total_requests or 0,
        "today_requests": today_requests or 0,
        "hour_requests": hour_requests or 0,
        "success_rate": round(rate, 1),
        "avg_response_ms": int(avg_time) if avg_time else 0,
        "queue": job_queue.stats,
    }
