"""Runtime settings endpoints."""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy import delete

from ..captcha.service import get_captcha_service
from ..config import settings, proxy_pool, ProxyEntry
from ..database import AsyncSessionLocal
from ..models import ProxySetting

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ProxyItem(BaseModel):
    host: str
    port: int
    user: str = ""
    password: str = ""
    type: str = "socks5"


class SettingsResponse(BaseModel):
    headless: bool
    max_concurrent: int
    cooldown: int
    cooldown_fail: int
    wait_delay: int
    proxy_enabled: bool
    proxies: List[ProxyItem]
    profile_strategy: str = "single"
    rotation_profile_count: int = 1


class UpdateSettings(BaseModel):
    headless: Optional[bool] = None
    max_concurrent: Optional[int] = None
    cooldown: Optional[int] = None
    cooldown_fail: Optional[int] = None
    wait_delay: Optional[int] = None
    proxy_enabled: Optional[bool] = None
    proxies: Optional[List[ProxyItem]] = None


def _build_response(svc) -> SettingsResponse:
    return SettingsResponse(
        headless=svc.headless,
        max_concurrent=settings.max_concurrent,
        cooldown=svc.cooldown,
        cooldown_fail=svc.cooldown_fail,
        wait_delay=svc.wait_delay,
        proxy_enabled=proxy_pool.enabled,
        proxies=[
            ProxyItem(host=p.host, port=p.port, user=p.user, password=p.password, type=p.proxy_type)
            for p in proxy_pool.proxies
        ],
        profile_strategy=settings.profile_strategy,
        rotation_profile_count=settings.rotation_profile_count,
    )


@router.get("", response_model=SettingsResponse)
async def get_settings():
    svc = get_captcha_service()
    return _build_response(svc)


@router.put("", response_model=SettingsResponse)
async def update_settings(req: UpdateSettings):
    svc = get_captcha_service()

    if req.headless is not None:
        svc.headless = req.headless
    if req.max_concurrent is not None:
        settings.max_concurrent = max(1, min(req.max_concurrent, 64))
        svc.set_concurrency(settings.max_concurrent)
    if req.cooldown is not None:
        svc.cooldown = max(0, min(req.cooldown, 300))
    if req.cooldown_fail is not None:
        svc.cooldown_fail = max(0, min(req.cooldown_fail, 600))
    if req.wait_delay is not None:
        svc.wait_delay = max(0, min(req.wait_delay, 60))
    if req.proxy_enabled is not None:
        proxy_pool.enabled = req.proxy_enabled
    if req.proxies is not None:
        entries = [
            ProxyEntry(host=p.host, port=p.port, user=p.user, password=p.password, proxy_type=p.type)
            for p in req.proxies
        ]
        proxy_pool.set_proxies(entries)

    if req.proxies is not None or req.proxy_enabled is not None:
        await _save_proxies_to_db()

    return _build_response(svc)


async def _save_proxies_to_db():
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(delete(ProxySetting))
            for i, p in enumerate(proxy_pool.proxies):
                session.add(ProxySetting(
                    host=p.host, port=p.port, user=p.user,
                    password=p.password, proxy_type=p.proxy_type,
                    enabled=proxy_pool.enabled, position=i,
                ))


@router.get("/profile-info")
async def get_profile_info():
    """Get current profile strategy info."""
    svc = get_captcha_service()
    return svc.profile_info


@router.post("/clear-data")
async def trigger_clear_data():
    """Manually trigger clear browsing data."""
    from ..captcha.clear_data import clear_all_data
    from ..captcha.profile_manager import get_profile_manager

    pm = get_profile_manager()
    profile_dirs = []
    cdp_ports = []
    for svc in pm._services:
        profile_dirs.append(svc.profile_dir)
        port = svc._cdp_port or getattr(svc, '_cdp_port_override', None)
        if port:
            cdp_ports.append(port)

    if not profile_dirs:
        return {"status": "error", "message": "No profiles found"}

    try:
        await clear_all_data(profile_dirs, cdp_ports)
        return {"status": "ok", "message": f"Browsing data cleared for {len(profile_dirs)} profile(s)"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
