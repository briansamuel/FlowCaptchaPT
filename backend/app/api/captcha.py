"""Captcha token endpoints."""
from __future__ import annotations
import asyncio
import sys
import time
import logging
from typing import Optional
from enum import Enum

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models import UsageLog
from ..captcha.service import get_captcha_service
from ..captcha.queue import job_queue, JobStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/captcha", tags=["captcha"])


class CaptchaAction(str, Enum):
    VIDEO_GENERATION = "VIDEO_GENERATION"
    IMAGE_GENERATION = "IMAGE_GENERATION"


class CaptchaRequest(BaseModel):
    action: CaptchaAction


class CaptchaResponse(BaseModel):
    token: Optional[str] = None
    error: Optional[str] = None
    success: bool = False
    job_id: Optional[str] = None
    status: Optional[str] = None
    callback_url: Optional[str] = None


class CallbackRequest(BaseModel):
    result: str  # "success" or "failed"
    error: Optional[str] = None


@router.post("", response_model=CaptchaResponse)
async def get_captcha_token(
    req: CaptchaRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Get reCAPTCHA token. No auth required."""
    start = time.time()

    client_ip = http_request.client.host if http_request.client else None
    service = get_captcha_service()

    result = await service.get_token(req.action.value)
    elapsed = int((time.time() - start) * 1000)

    # Cooldown — don't log, just queue
    if result.error and "Cooldown" in result.error:
        job = job_queue.submit(req.action.value, None, client_ip)
        asyncio.ensure_future(job_queue.run_worker(job, service))
        return CaptchaResponse(
            job_id=job.id,
            status=JobStatus.PENDING.value,
            error="Service busy, job queued",
            success=False,
        )

    # Log usage
    log = UsageLog(
        api_key_id=None,
        action=req.action.value,
        success=bool(result.token),
        error=result.error,
        token_preview=result.token[:20] if result.token else None,
        ip_address=client_ip,
        response_time_ms=elapsed,
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)

    if result.token:
        base_url = str(http_request.base_url).rstrip("/")
        return CaptchaResponse(
            token=result.token,
            success=True,
            status="completed",
            callback_url=f"{base_url}/api/captcha/callback/{log.id}",
        )

    return CaptchaResponse(error=result.error, success=False, status="failed")


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Poll job status."""
    job = job_queue.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found or expired")
    return job.to_dict()


@router.post("/callback/{log_id}")
async def captcha_callback(
    log_id: str,
    req: CallbackRequest,
    db: AsyncSession = Depends(get_db),
):
    """Report token success/failure."""
    log = await db.get(UsageLog, log_id)
    if not log:
        raise HTTPException(404, "Log not found")
    if req.result not in ("success", "failed"):
        raise HTTPException(422, "result must be 'success' or 'failed'")

    log.callback_result = req.result
    log.callback_error = req.error
    await db.commit()
    return {"ok": True, "message": f"Callback recorded: {req.result}"}


@router.post("/login")
async def open_login_browser():
    """
    Open Chrome (non-headless) for manual Google login.
    Close the browser window when done.
    """
    service = get_captcha_service()
    result = await service.open_for_login()
    return {"message": result}


@router.post("/import-cookies")
async def import_cookies(
    http_request: Request,
):
    """
    Import cookies into the Chrome profile.
    Body: { "url": "https://...", "cookies": [...] }
    Accepts cookie format from browser extensions (EditThisCookie, etc.)
    """
    import json
    from pathlib import Path
    from ..config import settings

    body = await http_request.json()
    url = body.get("url", "")
    raw_cookies = body.get("cookies", [])

    if not raw_cookies:
        raise HTTPException(400, "No cookies provided")

    # Normalize cookies for Playwright
    pw_cookies = []
    for c in raw_cookies:
        cookie = {
            "name": c.get("name", ""),
            "value": c.get("value", ""),
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
        }
        if c.get("expirationDate"):
            cookie["expires"] = c["expirationDate"]
        if "secure" in c:
            cookie["secure"] = c["secure"]
        if "httpOnly" in c:
            cookie["httpOnly"] = c["httpOnly"]
        same_site = c.get("sameSite", "Lax")
        if same_site in ("Strict", "Lax", "None"):
            cookie["sameSite"] = same_site
        elif isinstance(same_site, str) and same_site.lower() in ("strict", "lax", "none"):
            cookie["sameSite"] = same_site.capitalize()
        else:
            cookie["sameSite"] = "Lax"
        pw_cookies.append(cookie)

    # Save cookies to profile via Playwright
    import sys

    service = get_captcha_service()
    chrome_path = service._find_system_chrome()

    async def _inject():
        from playwright.async_api import async_playwright
        service._cleanup_locks()
        pw = await async_playwright().start()
        try:
            launch_args = {
                "headless": True,
                "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                "ignore_default_args": ["--enable-automation"],
            }
            if chrome_path:
                launch_args["executable_path"] = chrome_path
            ctx = await pw.chromium.launch_persistent_context(
                service.profile_dir,
                **launch_args,
            )
            await ctx.add_cookies(pw_cookies)
            # Verify
            check = await ctx.cookies(url) if url else await ctx.cookies()
            await ctx.close()
            return len(check)
        finally:
            await pw.stop()

    def run():
        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
        else:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_inject())
        finally:
            loop.close()

    loop = asyncio.get_running_loop()
    from concurrent.futures import ThreadPoolExecutor
    count = await loop.run_in_executor(None, run)

    logger.info(f"Imported {len(pw_cookies)} cookies for {url}, verified {count} in profile")
    return {
        "ok": True,
        "imported": len(pw_cookies),
        "verified": count,
        "message": f"Imported {len(pw_cookies)} cookies into Chrome profile",
    }
