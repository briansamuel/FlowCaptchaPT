"""Flow API endpoints - Image/Video generation via Google Labs Flow."""
from __future__ import annotations
import asyncio
import json
import logging
import time
import uuid
from typing import Optional, List, Dict

import aiohttp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

try:
    from aiohttp_socks import ProxyConnector
except ImportError:
    ProxyConnector = None

from ..captcha.service import get_captcha_service
from ..captcha.queue import job_queue, JobStatus
from ..config import proxy_pool

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/flow", tags=["flow"])

FLOW_API_BASE = "https://aisandbox-pa.googleapis.com/v1"
SESSION_REFRESH_URL = "https://labs.google/fx/api/auth/session"


# ---------------------------------------------------------------------------
# Token store: projectId -> {accessToken, cookies, expiresAt}
# ---------------------------------------------------------------------------

class TokenSession:
    def __init__(self, access_token: str, cookies: str, expires_at: float = 0):
        self.access_token = access_token
        self.cookies = cookies
        self.expires_at = expires_at

_token_store: Dict[str, TokenSession] = {}


async def _refresh_access_token(project_id: str) -> Optional[str]:
    session_data = _token_store.get(project_id)
    if not session_data or not session_data.cookies:
        return None

    tag = f"[{project_id[:8]}]"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                SESSION_REFRESH_URL,
                headers={
                    "Cookie": session_data.cookies,
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"{tag} Token refresh failed: HTTP {resp.status}")
                    return None
                data = await resp.json()
                new_token = data.get("access_token")
                if not new_token:
                    logger.warning(f"{tag} Token refresh: no access_token in response")
                    return None
                expires_str = data.get("expires", "")
                session_data.access_token = new_token
                logger.info(f"{tag} Token refreshed OK, expires={expires_str}")
                return new_token
    except Exception as e:
        logger.error(f"{tag} Token refresh error: {e}")
        return None


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ImageReference(BaseModel):
    base64: str
    mimeType: str = "image/jpeg"
    fileName: Optional[str] = None


class ImageGenerateRequest(BaseModel):
    accessToken: str
    projectId: str
    promptText: str
    aspectRatio: str = "IMAGE_ASPECT_RATIO_SQUARE"
    imageModel: str = "GEM_PIX_2"
    seed: Optional[int] = None
    # Single image (backward compatible)
    referenceImageBase64: Optional[str] = None
    referenceImageMimeType: str = "image/jpeg"
    referenceImageFileName: Optional[str] = None
    # Multiple images
    referenceImages: Optional[List[ImageReference]] = None


class ImageUpscaleRequest(BaseModel):
    accessToken: str
    projectId: str
    mediaId: str
    targetResolution: str = "UPSAMPLE_IMAGE_RESOLUTION_4K"


class ReferenceImage(BaseModel):
    base64: str
    mimeType: str = "image/jpeg"
    aspectRatio: str = "IMAGE_ASPECT_RATIO_LANDSCAPE"


class VideoGenerateRequest(BaseModel):
    accessToken: str
    projectId: str
    promptText: str
    aspectRatio: str = "VIDEO_ASPECT_RATIO_LANDSCAPE"
    videoModelKey: str = "veo_3_1_t2v_fast_4s"
    seed: Optional[int] = None
    # I2V: start frame
    startImageBase64: Optional[str] = None
    startImageMimeType: str = "image/jpeg"
    startImageAspectRatio: str = "IMAGE_ASPECT_RATIO_LANDSCAPE"
    # I2V-FL: end frame
    endImageBase64: Optional[str] = None
    endImageMimeType: str = "image/jpeg"
    endImageAspectRatio: str = "IMAGE_ASPECT_RATIO_LANDSCAPE"
    # R2V: reference images (1-3), uses separate endpoint
    referenceImages: Optional[List[ReferenceImage]] = None
    referenceAudio: str = "zephyr"


class VideoUpscaleRequest(BaseModel):
    accessToken: str
    projectId: str
    videoMediaId: str
    resolution: str = "VIDEO_RESOLUTION_1080P"
    aspectRatio: str = "VIDEO_ASPECT_RATIO_LANDSCAPE"
    videoModelKey: str = "veo_3_1_upsampler_1080p"
    seed: Optional[int] = None


class VideoStatusOperation(BaseModel):
    operationName: str
    sceneId: str = ""


class VideoStatusRequest(BaseModel):
    accessToken: str
    operations: List[VideoStatusOperation]


class SessionRegisterRequest(BaseModel):
    projectId: str
    accessToken: str
    cookies: str


# ---------------------------------------------------------------------------
# Session management endpoints
# ---------------------------------------------------------------------------

@router.post("/sessions/register")
async def register_session(req: SessionRegisterRequest):
    """Register session cookies for auto token refresh."""
    _token_store[req.projectId] = TokenSession(
        access_token=req.accessToken,
        cookies=req.cookies,
    )
    logger.info(f"[{req.projectId[:8]}] Session registered")
    return {"success": True, "projectId": req.projectId}


@router.get("/sessions/list")
async def list_sessions():
    """List registered sessions (no secrets)."""
    return {
        "sessions": [
            {"projectId": pid, "hasToken": bool(s.access_token), "hasCookies": bool(s.cookies)}
            for pid, s in _token_store.items()
        ]
    }


@router.delete("/sessions/{project_id}")
async def delete_session(project_id: str):
    """Remove a registered session."""
    if project_id in _token_store:
        del _token_store[project_id]
    return {"success": True}


@router.post("/sessions/refresh/{project_id}")
async def manual_refresh(project_id: str):
    """Manually trigger token refresh for a project."""
    new_token = await _refresh_access_token(project_id)
    if not new_token:
        raise HTTPException(400, "Token refresh failed - check cookies")
    return {"success": True, "accessToken": new_token}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CAPTCHA_MINT_TIMEOUT = 45
CAPTCHA_QUEUE_TIMEOUT = 60


async def _mint_recaptcha(action: str) -> str:
    service = get_captcha_service()
    try:
        result = await asyncio.wait_for(service.get_token(action), timeout=CAPTCHA_MINT_TIMEOUT)
    except asyncio.TimeoutError:
        raise HTTPException(503, f"reCAPTCHA mint timeout ({CAPTCHA_MINT_TIMEOUT}s)")

    if result.token:
        return result.token

    if result.error and "Cooldown" in result.error:
        job = job_queue.submit(action, None, None)
        asyncio.ensure_future(job_queue.run_worker(job, service))
        deadline = time.time() + CAPTCHA_QUEUE_TIMEOUT
        while time.time() < deadline:
            j = job_queue.get(job.id)
            if j and j.status == JobStatus.COMPLETED:
                return j.token
            if j and j.status in (JobStatus.FAILED, JobStatus.TIMEOUT):
                raise HTTPException(503, f"reCAPTCHA failed: {j.error}")
            await asyncio.sleep(1)
        raise HTTPException(503, "Timeout waiting for reCAPTCHA token")

    raise HTTPException(503, f"reCAPTCHA failed: {result.error}")


def _client_context(
    project_id: str,
    recaptcha_token: str,
    paygate_tier: Optional[str] = None,
) -> dict:
    ctx: dict = {
        "recaptchaContext": {
            "token": recaptcha_token,
            "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB",
        },
        "projectId": project_id,
        "tool": "PINHOLE",
        "sessionId": f";{int(time.time() * 1000)}",
    }
    if paygate_tier:
        ctx["userPaygateTier"] = paygate_tier
    return ctx


def _resolve_token(access_token: str, project_id: str = "") -> str:
    """Use stored token if available and request token is empty/placeholder."""
    if project_id and project_id in _token_store:
        stored = _token_store[project_id]
        if stored.access_token:
            return stored.access_token
    return access_token


def _headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "text/plain;charset=UTF-8",
        "Origin": "https://labs.google",
        "Referer": "https://labs.google/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36 Edg/137.0.0.0",
        "sec-ch-ua": '"Microsoft Edge";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "cross-site",
    }


FLOW_MAX_RETRIES = 3
FLOW_RETRY_DELAY = 10

ERROR_MESSAGES_VI = {
    # Policy / Content filters
    "PUBLIC_ERROR_PROMINENT_PEOPLE_UPLOAD": "Ảnh chứa người nổi tiếng - Google không cho phép upload",
    "PUBLIC_ERROR_PROMINENT_PEOPLE": "Video chứa người nổi tiếng - không được phép sử dụng",
    "PUBLIC_ERROR_PROMINENT_PEOPLE_FILTER_FAILED": "Nội dung chứa người nổi tiếng - không cho phép tạo",
    "PUBLIC_ERROR_SEXUAL": "Vi phạm chính sách nội dung người lớn",
    "PUBLIC_ERROR_UNSAFE": "Nội dung không an toàn - vi phạm chính sách",
    "PUBLIC_ERROR_UNSAFE_GENERATION": "Nội dung không an toàn - vi phạm chính sách",
    "PUBLIC_ERROR_VIOLENCE": "Nội dung chứa bạo lực",
    "PUBLIC_ERROR_IP_INPUT_IMAGE": "Ảnh vi phạm bản quyền sở hữu trí tuệ (IP)",
    "PUBLIC_ERROR_MINOR_UPLOAD": "Ảnh chứa trẻ vị thành niên - không cho phép upload",
    "PUBLIC_ERROR_CHILD_SAFETY": "Vi phạm chính sách bảo vệ trẻ em",
    "PUBLIC_ERROR_DECEPTIVE": "Nội dung lừa đảo hoặc gây hiểu lầm",
    "PUBLIC_ERROR_HATE_SPEECH": "Nội dung chứa ngôn từ thù địch",
    "PUBLIC_ERROR_DANGEROUS": "Nội dung nguy hiểm hoặc có hại",
    "PUBLIC_ERROR_AUDIO_FILTERED": "Audio bị lọc do vi phạm chính sách",
    "PUBLIC_ERROR_NSFW_FILTER_TRIGGERED": "Vi phạm chính sách nội dung người lớn",
    "PUBLIC_ERROR_SAFETY_FILTER_TRIGGERED": "Nội dung bị chặn bởi bộ lọc an toàn",
    "SEXUALLY_EXPLICIT": "Vi phạm chính sách nội dung người lớn",
    # Generation errors
    "PUBLIC_ERROR_IMAGE_GENERATION_FAILED": "Tạo ảnh thất bại",
    "PUBLIC_ERROR_VIDEO_GENERATION_FAILED": "Tạo video thất bại",
    # Quota / Auth / Rate
    "PUBLIC_ERROR_UNUSUAL_ACTIVITY": "Hoạt động bất thường, đang thử lại...",
    "PUBLIC_ERROR_UNUSUAL_ACTIVITY_TOO_MUCH_TRAFFIC": "Quá nhiều request, vui lòng chờ 20s",
    "PUBLIC_ERROR_RATE_LIMIT_EXCEEDED": "Vượt quá giới hạn tốc độ, vui lòng thử lại sau",
    "PUBLIC_ERROR_QUOTA_EXCEEDED": "Hết credit, vui lòng nạp thêm",
}


def _parse_flow_error(text: str, status_code: int) -> tuple[str, str, bool]:
    """Parse Flow API error. Returns (reason, vi_message, is_retryable)."""
    reason = ""
    try:
        data = json.loads(text)
        details = data.get("error", {}).get("details", [])
        for d in details:
            if d.get("reason"):
                reason = d["reason"]
                break
    except (json.JSONDecodeError, KeyError):
        pass

    is_retryable = status_code == 503
    vi_msg = ERROR_MESSAGES_VI.get(reason)
    if not vi_msg:
        lower = text.lower()
        if "content moderation" in lower or "sexually_explicit" in lower:
            vi_msg = "Nội dung không vượt qua kiểm duyệt"
        elif status_code == 401:
            vi_msg = "Token xác thực hết hạn hoặc không hợp lệ"
        elif status_code == 403:
            vi_msg = "Không có quyền truy cập"
        elif "upload" in lower:
            vi_msg = "Lỗi upload ảnh"
        elif "image" in lower:
            vi_msg = "Lỗi tạo ảnh"
        else:
            vi_msg = "Lỗi tạo nội dung"

    return reason, vi_msg, is_retryable


async def _flow_request(
    method: str,
    url: str,
    access_token: str,
    body: dict = None,
    timeout_s: int = 120,
    project_id: str = "",
) -> dict:
    tag = f"[{project_id[:8]}]" if project_id else ""
    current_token = access_token
    token_refreshed = False

    connector = None
    proxy_entry = proxy_pool.next()
    if proxy_entry and ProxyConnector:
        connector = ProxyConnector.from_url(proxy_entry.url)
        logger.debug(f"{tag} Using proxy: {proxy_entry.host}:{proxy_entry.port}")

    # Extract recaptcha token preview for correlation (first 30 chars)
    rcap_preview = ""
    session_id = ""
    batch_id = ""
    if body:
        try:
            ctx = body.get("clientContext") or (body.get("requests", [{}])[0].get("clientContext") if body.get("requests") else {})
            rcap = ctx.get("recaptchaContext", {}).get("token", "")
            rcap_preview = rcap[:30] if rcap else "<no-token>"
            session_id = ctx.get("sessionId", "")
            batch_id = body.get("mediaGenerationContext", {}).get("batchId", "")
        except Exception:
            pass

    endpoint_short = url.split("/v1/")[-1][:60] if "/v1/" in url else url[-60:]

    async with aiohttp.ClientSession(connector=connector) as session:
        for attempt in range(1, FLOW_MAX_RETRIES + 1):
            req_start = time.time()
            try:
                async with session.request(
                    method,
                    url,
                    headers=_headers(current_token),
                    json=body,
                    timeout=aiohttp.ClientTimeout(total=timeout_s),
                ) as resp:
                    elapsed_ms = int((time.time() - req_start) * 1000)
                    # Capture key Google response headers
                    google_headers = {
                        k: v for k, v in resp.headers.items()
                        if k.lower().startswith(("x-google", "x-goog", "x-debug", "alt-svc", "server", "via"))
                    }
                    if resp.status == 200:
                        logger.info(
                            f"{tag} ✅ {endpoint_short} {resp.status} {elapsed_ms}ms | "
                            f"sid={session_id} batch={batch_id[:8]} rcap={rcap_preview}..."
                        )
                        return await resp.json()
                    text = await resp.text()
                    reason, vi_msg, is_retryable = _parse_flow_error(text, resp.status)
                    logger.error(
                        f"{tag} ❌ {endpoint_short} {resp.status} [{reason}] {elapsed_ms}ms | "
                        f"sid={session_id} batch={batch_id[:8]} rcap={rcap_preview}... | "
                        f"google_headers={google_headers} | "
                        f"body_preview={text[:400]}"
                    )

                    if resp.status == 401 and not token_refreshed and project_id:
                        logger.info(f"{tag} Token expired, attempting auto-refresh...")
                        new_token = await _refresh_access_token(project_id)
                        if new_token:
                            current_token = new_token
                            token_refreshed = True
                            logger.info(f"{tag} Token refreshed, retrying request...")
                            continue
                        logger.warning(f"{tag} Token refresh failed, no cookies registered")

                    if reason == "PUBLIC_ERROR_UNUSUAL_ACTIVITY_TOO_MUCH_TRAFFIC":
                        logger.warning(f"{tag} Flow API rate-limited [{reason}]: {vi_msg} (no retry, no delay — fail fast)")
                        raise HTTPException(resp.status, vi_msg)
                    if is_retryable and attempt < FLOW_MAX_RETRIES:
                        logger.warning(
                            f"{tag} Flow API {resp.status} [{reason}] (lần {attempt}/{FLOW_MAX_RETRIES}): {vi_msg}. "
                            f"Thử lại sau {FLOW_RETRY_DELAY}s..."
                        )
                        await asyncio.sleep(FLOW_RETRY_DELAY)
                        continue
                    raise HTTPException(resp.status, vi_msg)
            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                if attempt < FLOW_MAX_RETRIES:
                    logger.warning(f"{tag} Flow API connection error (lần {attempt}/{FLOW_MAX_RETRIES}): {e}. Thử lại...")
                    await asyncio.sleep(FLOW_RETRY_DELAY)
                    continue
                logger.error(f"{tag} Flow API connection error: {e}")
                raise HTTPException(502, f"Lỗi kết nối tới Google API: {type(e).__name__}")


# ---------------------------------------------------------------------------
# Upload helpers (no reCAPTCHA required)
# ---------------------------------------------------------------------------

async def _upload_reference_image(
    access_token: str,
    project_id: str,
    image_b64: str,
    mime_type: str = "image/jpeg",
    file_name: Optional[str] = None,
) -> str:
    if not file_name:
        file_name = f"upload_{uuid.uuid4().hex[:12]}.jpg"
    body = {
        "clientContext": {
            "projectId": project_id,
            "tool": "PINHOLE",
            "sessionId": f";{int(time.time() * 1000)}",
        },
        "fileName": file_name,
        "imageBytes": image_b64,
        "isHidden": False,
        "isUserUploaded": True,
        "mimeType": mime_type,
    }
    result = await _flow_request(
        "POST", f"{FLOW_API_BASE}/flow/uploadImage", access_token, body,
        project_id=project_id,
    )
    media_id = result.get("media", {}).get("name")
    if not media_id:
        raise HTTPException(500, "Upload reference image failed: no mediaId")
    logger.info(f"Uploaded reference image: {media_id}")
    return media_id


async def _upload_user_image(
    access_token: str,
    image_b64: str,
    aspect_ratio: str = "IMAGE_ASPECT_RATIO_LANDSCAPE",
    mime_type: str = "image/jpeg",
) -> str:
    body = {
        "clientContext": {
            "sessionId": f";{int(time.time() * 1000)}",
            "tool": "ASSET_MANAGER",
        },
        "imageInput": {
            "aspectRatio": aspect_ratio,
            "isUserUploaded": True,
            "mimeType": mime_type,
            "rawImageBytes": image_b64,
        },
    }
    result = await _flow_request(
        "POST", f"{FLOW_API_BASE}:uploadUserImage", access_token, body,
    )
    media_id = result.get("mediaGenerationId", {}).get("mediaGenerationId")
    if not media_id:
        raise HTTPException(500, "Upload user image failed: no mediaGenerationId")
    logger.info(f"Uploaded user image: {media_id}")
    return media_id


# ---------------------------------------------------------------------------
# Image endpoints
# ---------------------------------------------------------------------------

@router.post("/images/generate")
async def generate_image(req: ImageGenerateRequest):
    """Generate image (T2I). Include referenceImages or referenceImageBase64 for I2I."""
    token = _resolve_token(req.accessToken, req.projectId)
    image_inputs: list = []
    upload_tasks: list = []

    if req.referenceImages:
        for ref in req.referenceImages:
            upload_tasks.append(
                _upload_reference_image(
                    token, req.projectId,
                    ref.base64, ref.mimeType, ref.fileName,
                )
            )
    elif req.referenceImageBase64:
        upload_tasks.append(
            _upload_reference_image(
                token, req.projectId,
                req.referenceImageBase64, req.referenceImageMimeType,
                req.referenceImageFileName,
            )
        )

    if upload_tasks:
        ref_ids = await asyncio.gather(*upload_tasks)
        for ref_id in ref_ids:
            image_inputs.append({
                "name": ref_id,
                "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE",
            })

    recaptcha = await _mint_recaptcha("IMAGE_GENERATION")
    ctx = _client_context(req.projectId, recaptcha)
    seed = req.seed if req.seed is not None else int(time.time() * 1000) % 1000000
    batch_id = str(uuid.uuid4())

    body = {
        "clientContext": ctx,
        "mediaGenerationContext": {"batchId": batch_id},
        "useNewMedia": True,
        "requests": [{
            "clientContext": ctx,
            "imageModelName": req.imageModel,
            "imageAspectRatio": req.aspectRatio,
            "structuredPrompt": {"parts": [{"text": req.promptText}]},
            "seed": seed,
            "imageInputs": image_inputs,
        }],
    }

    result = await _flow_request(
        "POST",
        f"{FLOW_API_BASE}/projects/{req.projectId}/flowMedia:batchGenerateImages",
        token,
        body,
        project_id=req.projectId,
    )

    media_out = []
    for m in result.get("media", []):
        gen = m.get("image", {}).get("generatedImage", {})
        media_out.append({
            "mediaName": m.get("name"),
            "mediaId": gen.get("mediaId"),
            "fifeUrl": gen.get("fifeUrl"),
            "seed": gen.get("seed"),
            "prompt": gen.get("prompt"),
            "aspectRatio": gen.get("aspectRatio"),
            "dimensions": m.get("image", {}).get("dimensions"),
        })

    return {
        "success": True,
        "batchId": batch_id,
        "media": media_out,
        "remainingCredits": result.get("remainingCredits"),
    }


UPSCALE_MAX_RETRIES = 5
UPSCALE_RETRY_DELAY = 8


@router.post("/images/generate-v2")
async def generate_image_v2(req: ImageGenerateRequest):
    """Generate image V2 — matches current web UI payload.

    Default imageModel changed to NARWHAL (vs GEM_PIX_2 in V1).
    Same payload structure as V1 (already matches).
    """
    token = _resolve_token(req.accessToken, req.projectId)
    image_inputs: list = []
    upload_tasks: list = []

    if req.referenceImages:
        for ref in req.referenceImages:
            upload_tasks.append(
                _upload_reference_image(
                    token, req.projectId,
                    ref.base64, ref.mimeType, ref.fileName,
                )
            )
    elif req.referenceImageBase64:
        upload_tasks.append(
            _upload_reference_image(
                token, req.projectId,
                req.referenceImageBase64, req.referenceImageMimeType,
                req.referenceImageFileName,
            )
        )

    if upload_tasks:
        ref_ids = await asyncio.gather(*upload_tasks)
        for ref_id in ref_ids:
            image_inputs.append({
                "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE",
                "name": ref_id,
            })

    recaptcha = await _mint_recaptcha("IMAGE_GENERATION")
    ctx = _client_context(req.projectId, recaptcha)
    seed = req.seed if req.seed is not None else int(time.time() * 1000) % 1000000
    batch_id = str(uuid.uuid4())

    # V2 default model: NARWHAL (web UI default). Allow override via req.imageModel.
    image_model = req.imageModel if req.imageModel and req.imageModel != "GEM_PIX_2" else "NARWHAL"

    body = {
        "clientContext": ctx,
        "mediaGenerationContext": {"batchId": batch_id},
        "requests": [{
            "clientContext": ctx,
            "imageAspectRatio": req.aspectRatio,
            "imageInputs": image_inputs,
            "imageModelName": image_model,
            "seed": seed,
            "structuredPrompt": {"parts": [{"text": req.promptText}]},
        }],
        "useNewMedia": True,
    }

    result = await _flow_request(
        "POST",
        f"{FLOW_API_BASE}/projects/{req.projectId}/flowMedia:batchGenerateImages",
        token,
        body,
        project_id=req.projectId,
    )

    media_out = []
    for m in result.get("media", []):
        gen = m.get("image", {}).get("generatedImage", {})
        media_out.append({
            "mediaName": m.get("name"),
            "mediaId": gen.get("mediaId"),
            "fifeUrl": gen.get("fifeUrl"),
            "seed": gen.get("seed"),
            "prompt": gen.get("prompt"),
            "aspectRatio": gen.get("aspectRatio"),
            "dimensions": m.get("image", {}).get("dimensions"),
        })

    return {
        "success": True,
        "batchId": batch_id,
        "media": media_out,
        "remainingCredits": result.get("remainingCredits"),
    }


@router.post("/images/upscale")
async def upscale_image(req: ImageUpscaleRequest):
    """Upscale image to 2K/4K. Returns base64 JPEG. Retries on UNUSUAL_ACTIVITY with fresh captcha."""
    token = _resolve_token(req.accessToken, req.projectId)
    tag = f"[{req.projectId[:8]}]"

    last_err: Optional[HTTPException] = None
    for attempt in range(1, UPSCALE_MAX_RETRIES + 1):
        recaptcha = await _mint_recaptcha("IMAGE_GENERATION")
        ctx = _client_context(req.projectId, recaptcha, paygate_tier="PAYGATE_TIER_TWO")
        body = {
            "mediaId": req.mediaId,
            "targetResolution": req.targetResolution,
            "clientContext": ctx,
        }
        try:
            result = await _flow_request(
                "POST",
                f"{FLOW_API_BASE}/flow/upsampleImage",
                token,
                body,
                timeout_s=300,
                project_id=req.projectId,
            )
            if attempt > 1:
                logger.info(f"{tag} Upscale OK after {attempt} attempts")
            return {"success": True, "encodedImage": result.get("encodedImage")}
        except HTTPException as e:
            last_err = e
            detail = str(e.detail) if e.detail else ""
            # Don't retry on rate-limit (429 TOO_MUCH_TRAFFIC) — that just spams Google
            is_rate_limited = e.status_code == 429 or "Quá nhiều request" in detail
            if is_rate_limited:
                logger.warning(f"{tag} Upscale rate-limited (429), failing fast (no retry to avoid spam)")
                raise
            # Retry only on score-based failures (UNUSUAL_ACTIVITY 403) or transient 503
            is_retryable = "bất thường" in detail or e.status_code == 503
            if is_retryable and attempt < UPSCALE_MAX_RETRIES:
                logger.warning(f"{tag} Upscale attempt {attempt}/{UPSCALE_MAX_RETRIES} failed: {detail}. Retry sau {UPSCALE_RETRY_DELAY}s với captcha mới...")
                await asyncio.sleep(UPSCALE_RETRY_DELAY)
                continue
            raise

    if last_err:
        raise last_err
    raise HTTPException(503, "Upscale failed after all retries")


# ---------------------------------------------------------------------------
# Video endpoints
# ---------------------------------------------------------------------------

@router.post("/videos/generate")
async def generate_video(req: VideoGenerateRequest):
    """Generate video. Modes:
    - T2V: no images, text prompt only
    - I2V: startImageBase64 only
    - I2V-FL: startImageBase64 + endImageBase64 (first-last frame)
    - R2V: referenceImages (1-3 images, separate endpoint + V2 payload)
    """
    token = _resolve_token(req.accessToken, req.projectId)
    is_r2v = bool(req.referenceImages)

    # --- Upload images ---
    upload_tasks: list = []
    start_media_id: Optional[str] = None
    end_media_id: Optional[str] = None
    ref_media_ids: list = []

    if is_r2v:
        for ref in req.referenceImages:
            upload_tasks.append(
                _upload_user_image(
                    token, ref.base64, ref.aspectRatio, ref.mimeType,
                )
            )
    else:
        if req.startImageBase64:
            upload_tasks.append(
                _upload_user_image(
                    token, req.startImageBase64,
                    req.startImageAspectRatio, req.startImageMimeType,
                )
            )
        if req.endImageBase64:
            upload_tasks.append(
                _upload_user_image(
                    token, req.endImageBase64,
                    req.endImageAspectRatio, req.endImageMimeType,
                )
            )

    if upload_tasks:
        uploaded = await asyncio.gather(*upload_tasks)
        if is_r2v:
            ref_media_ids = list(uploaded)
        else:
            idx = 0
            if req.startImageBase64:
                start_media_id = uploaded[idx]
                idx += 1
            if req.endImageBase64:
                end_media_id = uploaded[idx]

    # --- Mint reCAPTCHA ---
    recaptcha = await _mint_recaptcha("VIDEO_GENERATION")
    ctx = _client_context(req.projectId, recaptcha, paygate_tier="PAYGATE_TIER_TWO")
    seed = req.seed if req.seed is not None else int(time.time() * 1000) % 1000000
    batch_id = str(uuid.uuid4())

    if is_r2v:
        # R2V: V2 payload with structuredPrompt, referenceImages, referenceAudio
        req_item: dict = {
            "aspectRatio": req.aspectRatio,
            "seed": seed,
            "textInput": {
                "structuredPrompt": {"parts": [{"text": req.promptText}]},
            },
            "videoModelKey": req.videoModelKey,
            "metadata": {},
            "referenceImages": [
                {"mediaId": mid, "imageUsageType": "IMAGE_USAGE_TYPE_ASSET"}
                for mid in ref_media_ids
            ],
            "referenceAudio": [{"mediaId": req.referenceAudio}],
        }
        endpoint = f"{FLOW_API_BASE}/video:batchAsyncGenerateVideoReferenceImages"
        body = {
            "mediaGenerationContext": {"batchId": batch_id},
            "clientContext": ctx,
            "requests": [req_item],
            "useV2ModelConfig": True,
        }
    else:
        # T2V / I2V / I2V-FL: V1 payload with plain prompt
        req_item = {
            "aspectRatio": req.aspectRatio,
            "seed": seed,
            "textInput": {"prompt": req.promptText},
            "videoModelKey": req.videoModelKey,
            "metadata": {},
        }
        if start_media_id and end_media_id:
            endpoint = f"{FLOW_API_BASE}/video:batchAsyncGenerateVideoStartAndEndImage"
            req_item["startImage"] = {"mediaId": start_media_id}
            req_item["endImage"] = {"mediaId": end_media_id}
        elif start_media_id:
            endpoint = f"{FLOW_API_BASE}/video:batchAsyncGenerateVideoStartImage"
            req_item["startImage"] = {"mediaId": start_media_id}
        else:
            endpoint = f"{FLOW_API_BASE}/video:batchAsyncGenerateVideoText"
        body = {
            "clientContext": ctx,
            "requests": [req_item],
        }

    result = await _flow_request("POST", endpoint, token, body, project_id=req.projectId)

    operations = []
    for op in result.get("operations", []):
        operations.append({
            "operationName": op.get("operation", {}).get("name"),
            "sceneId": op.get("sceneId"),
        })

    return {
        "success": True,
        "batchId": batch_id,
        "operations": operations,
        "remainingCredits": result.get("remainingCredits"),
    }


@router.post("/videos/generate-v2")
async def generate_video_v2(req: VideoGenerateRequest):
    """Generate video with V2 payload schema (matches current web UI).

    Differences from /videos/generate:
    - Uses `structuredPrompt: {parts: [{text}]}` instead of `prompt`
    - Adds `mediaGenerationContext.audioFailurePreference = "BLOCK_SILENCED_VIDEOS"`
    - Adds top-level `useV2ModelConfig: true` for all modes
    - Modes supported: T2V, I2V, I2V-FL, R2V
    """
    token = _resolve_token(req.accessToken, req.projectId)
    is_r2v = bool(req.referenceImages)

    # --- Upload images ---
    upload_tasks: list = []
    start_media_id: Optional[str] = None
    end_media_id: Optional[str] = None
    ref_media_ids: list = []

    if is_r2v:
        for ref in req.referenceImages:
            upload_tasks.append(
                _upload_user_image(token, ref.base64, ref.aspectRatio, ref.mimeType)
            )
    else:
        if req.startImageBase64:
            upload_tasks.append(
                _upload_user_image(token, req.startImageBase64, req.startImageAspectRatio, req.startImageMimeType)
            )
        if req.endImageBase64:
            upload_tasks.append(
                _upload_user_image(token, req.endImageBase64, req.endImageAspectRatio, req.endImageMimeType)
            )

    if upload_tasks:
        uploaded = await asyncio.gather(*upload_tasks)
        if is_r2v:
            ref_media_ids = list(uploaded)
        else:
            idx = 0
            if req.startImageBase64:
                start_media_id = uploaded[idx]
                idx += 1
            if req.endImageBase64:
                end_media_id = uploaded[idx]

    recaptcha = await _mint_recaptcha("VIDEO_GENERATION")
    ctx = _client_context(req.projectId, recaptcha, paygate_tier="PAYGATE_TIER_TWO")
    seed = req.seed if req.seed is not None else int(time.time() * 1000) % 1000000
    batch_id = str(uuid.uuid4())

    # V2 request item — always uses structuredPrompt
    req_item: dict = {
        "aspectRatio": req.aspectRatio,
        "metadata": {},
        "seed": seed,
        "textInput": {
            "structuredPrompt": {"parts": [{"text": req.promptText}]},
        },
        "videoModelKey": req.videoModelKey,
    }

    if is_r2v:
        req_item["referenceImages"] = [
            {"mediaId": mid, "imageUsageType": "IMAGE_USAGE_TYPE_ASSET"}
            for mid in ref_media_ids
        ]
        req_item["referenceAudio"] = [{"mediaId": req.referenceAudio}]
        endpoint = f"{FLOW_API_BASE}/video:batchAsyncGenerateVideoReferenceImages"
    elif start_media_id and end_media_id:
        req_item["startImage"] = {"mediaId": start_media_id}
        req_item["endImage"] = {"mediaId": end_media_id}
        endpoint = f"{FLOW_API_BASE}/video:batchAsyncGenerateVideoStartAndEndImage"
    elif start_media_id:
        req_item["startImage"] = {"mediaId": start_media_id}
        endpoint = f"{FLOW_API_BASE}/video:batchAsyncGenerateVideoStartImage"
    else:
        endpoint = f"{FLOW_API_BASE}/video:batchAsyncGenerateVideoText"

    body = {
        "clientContext": ctx,
        "mediaGenerationContext": {
            "batchId": batch_id,
            "audioFailurePreference": "BLOCK_SILENCED_VIDEOS",
        },
        "requests": [req_item],
        "useV2ModelConfig": True,
    }

    result = await _flow_request("POST", endpoint, token, body, project_id=req.projectId)

    operations = []
    for op in result.get("operations", []):
        operations.append({
            "operationName": op.get("operation", {}).get("name"),
            "sceneId": op.get("sceneId"),
        })

    return {
        "success": True,
        "batchId": batch_id,
        "operations": operations,
        "remainingCredits": result.get("remainingCredits"),
    }


@router.post("/videos/upscale")
async def upscale_video(req: VideoUpscaleRequest):
    """Upscale video to 1080p/4K. Returns operation for polling."""
    token = _resolve_token(req.accessToken, req.projectId)
    recaptcha = await _mint_recaptcha("VIDEO_GENERATION")
    ctx = _client_context(req.projectId, recaptcha, paygate_tier="PAYGATE_TIER_TWO")
    seed = req.seed if req.seed is not None else int(time.time() * 1000) % 1000000
    batch_id = str(uuid.uuid4())

    body = {
        "mediaGenerationContext": {"batchId": batch_id},
        "clientContext": ctx,
        "requests": [{
            "resolution": req.resolution,
            "aspectRatio": req.aspectRatio,
            "seed": seed,
            "videoModelKey": req.videoModelKey,
            "metadata": {"workflowId": str(uuid.uuid4())},
            "videoInput": {"mediaId": req.videoMediaId},
        }],
        "useV2ModelConfig": True,
    }

    result = await _flow_request(
        "POST",
        f"{FLOW_API_BASE}/video:batchAsyncGenerateVideoUpsampleVideo",
        token,
        body,
        project_id=req.projectId,
    )

    operations = []
    for op in result.get("operations", []):
        operations.append({
            "operationName": op.get("operation", {}).get("name"),
            "sceneId": op.get("sceneId"),
        })

    return {
        "success": True,
        "batchId": batch_id,
        "operations": operations,
        "remainingCredits": result.get("remainingCredits"),
    }


@router.post("/videos/status")
async def check_video_status(req: VideoStatusRequest):
    """Poll video generation / upscale status."""
    ops = []
    for op in req.operations:
        ops.append({
            "operation": {"name": op.operationName},
            "sceneId": op.sceneId,
            "status": "MEDIA_GENERATION_STATUS_PENDING",
        })

    result = await _flow_request(
        "POST",
        f"{FLOW_API_BASE}/video:batchCheckAsyncVideoGenerationStatus",
        req.accessToken,
        {"operations": ops},
    )

    statuses = []
    for op in result.get("operations", []):
        operation = op.get("operation", {})
        metadata = operation.get("metadata", {})
        video = metadata.get("video", {})
        info: dict = {
            "status": op.get("status"),
            "operationName": operation.get("name"),
            "sceneId": op.get("sceneId"),
            "mediaGenerationId": op.get("mediaGenerationId"),
        }
        if video:
            info["video"] = {
                "fifeUrl": video.get("fifeUrl"),
                "servingBaseUri": video.get("servingBaseUri"),
                "mediaId": operation.get("name"),
                "seed": video.get("seed"),
                "prompt": video.get("prompt"),
                "model": video.get("model"),
                "aspectRatio": video.get("aspectRatio"),
            }
        statuses.append(info)

    in_progress = {"MEDIA_GENERATION_STATUS_PENDING", "MEDIA_GENERATION_STATUS_ACTIVE"}
    completed = all(s["status"] not in in_progress for s in statuses)

    remaining_credits = result.get("remainingCredits")

    return {
        "success": True,
        "completed": completed,
        "operations": statuses,
        "remainingCredits": remaining_credits,
    }
