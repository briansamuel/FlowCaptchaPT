"""Runtime settings endpoints."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..captcha.service import get_captcha_service
from ..config import settings
from .auth import require_admin

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsResponse(BaseModel):
    headless: bool
    max_concurrent: int
    cooldown: int
    cooldown_fail: int
    wait_delay: int


class UpdateSettings(BaseModel):
    headless: bool = None
    max_concurrent: int = None
    cooldown: int = None
    cooldown_fail: int = None
    wait_delay: int = None


@router.get("", response_model=SettingsResponse)
async def get_settings(_=Depends(require_admin)):
    svc = get_captcha_service()
    return SettingsResponse(
        headless=svc.headless,
        max_concurrent=settings.max_concurrent,
        cooldown=svc.cooldown,
        cooldown_fail=svc.cooldown_fail,
        wait_delay=svc.wait_delay,
    )


@router.put("", response_model=SettingsResponse)
async def update_settings(req: UpdateSettings, _=Depends(require_admin)):
    svc = get_captcha_service()

    if req.headless is not None:
        svc.headless = req.headless
    if req.max_concurrent is not None:
        settings.max_concurrent = max(1, min(req.max_concurrent, 10))
        svc.set_concurrency(settings.max_concurrent)
    if req.cooldown is not None:
        svc.cooldown = max(0, min(req.cooldown, 300))
    if req.cooldown_fail is not None:
        svc.cooldown_fail = max(0, min(req.cooldown_fail, 600))
    if req.wait_delay is not None:
        svc.wait_delay = max(0, min(req.wait_delay, 60))

    return SettingsResponse(
        headless=svc.headless,
        max_concurrent=settings.max_concurrent,
        cooldown=svc.cooldown,
        cooldown_fail=svc.cooldown_fail,
        wait_delay=svc.wait_delay,
    )
