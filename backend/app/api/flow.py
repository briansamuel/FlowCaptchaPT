"""Flow API endpoints - Image/Video generation via Google Labs Flow."""
from __future__ import annotations
import asyncio
import base64
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
        self.last_used: float = 0
        self.use_count: int = 0

_token_store: Dict[str, TokenSession] = {}


class SessionRotator:
    """Round-robin session selector with least-recently-used fallback."""

    def __init__(self):
        self._index: int = 0

    def next(self) -> Optional[str]:
        """Pick next session using round-robin + LRU.
        Returns projectId or None if no sessions available.
        """
        if not _token_store:
            return None

        sessions = list(_token_store.items())
        # Filter only sessions with valid tokens
        valid = [(pid, s) for pid, s in sessions if s.access_token]
        if not valid:
            return None

        # Sort by last_used time (ascending) to pick least recently used
        valid.sort(key=lambda x: x[1].last_used)

        # Pick the least recently used session
        chosen_pid, chosen_session = valid[0]
        chosen_session.last_used = time.time()
        chosen_session.use_count += 1
        logger.debug(f"SessionRotator: picked {chosen_pid[:8]}... (used {chosen_session.use_count}x)")
        return chosen_pid


_session_rotator = SessionRotator()


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
    accessToken: str = ""
    projectId: str = "auto"
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


class ReferenceVideo(BaseModel):
    base64: str
    mimeType: str = "video/mp4"


OMNI_MODELS = {"abra_edit"}


class VideoGenerateRequest(BaseModel):
    accessToken: str = ""
    projectId: str = "auto"
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
    # R2V / Edit: separate fields for images, videos, audio
    referenceImages: Optional[List[ReferenceImage]] = None
    referenceVideos: Optional[List[ReferenceVideo]] = None
    referenceAudio: Optional[str] = None
    # Edit Video: frame range (only used when referenceVideos present)
    startFrameIndex: int = 0
    endFrameIndex: int = 240


class VideoEditRequest(BaseModel):
    accessToken: str = ""
    projectId: str = "auto"
    promptText: str
    aspectRatio: str = "VIDEO_ASPECT_RATIO_LANDSCAPE"
    videoModelKey: str = "abra_edit"
    seed: Optional[int] = None
    # Source video to edit
    videoMediaId: str
    startFrameIndex: int = 0
    endFrameIndex: int = 240
    # Reference images (optional, 1-3)
    referenceImages: Optional[List[ReferenceImage]] = None
    referenceAudio: Optional[str] = None


class VideoUploadRequest(BaseModel):
    accessToken: str = ""
    projectId: str = "auto"
    videoBase64: str
    mimeType: str = "video/mp4"
    fileName: Optional[str] = None


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


@router.get("/sessions/next")
async def next_session():
    """Get next session in round-robin rotation (for external clients)."""
    picked = _session_rotator.next()
    if not picked:
        raise HTTPException(404, "No sessions available")
    return {"projectId": picked}


@router.get("/sessions/stats")
async def session_stats():
    """Get usage stats for all sessions."""
    stats = []
    for pid, s in _token_store.items():
        stats.append({
            "projectId": pid,
            "hasToken": bool(s.access_token),
            "hasCookies": bool(s.cookies),
            "lastUsed": s.last_used,
            "useCount": s.use_count,
        })
    stats.sort(key=lambda x: x["lastUsed"], reverse=True)
    return {"sessions": stats, "total": len(stats)}


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
            stored.last_used = time.time()
            stored.use_count += 1
            return stored.access_token
    return access_token


def _pick_session(project_id: str, access_token: str) -> tuple[str, str]:
    """Pick session with round-robin rotation.
    If projectId is "auto" or empty, rotate through available sessions.
    Returns (resolved_project_id, resolved_access_token).
    """
    if project_id and project_id != "auto" and project_id in _token_store:
        token = _resolve_token(access_token, project_id)
        return project_id, token

    if project_id and project_id != "auto" and access_token:
        return project_id, access_token

    picked_pid = _session_rotator.next()
    if picked_pid:
        stored = _token_store[picked_pid]
        logger.info(f"Auto-rotate: picked session {picked_pid[:8]}...")
        return picked_pid, stored.access_token

    if access_token:
        return project_id, access_token

    raise HTTPException(401, detail={
        "success": False,
        "error": "CREDENTIALS_MISSING",
        "message": "Không có session nào. Truyền accessToken + projectId hoặc đăng ký session trước.",
        "statusCode": 401,
    })


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
    "PUBLIC_ERROR_IMAGE_OUTPUT_IP_FILTER": "Ảnh đầu ra vi phạm bản quyền sở hữu trí tuệ (IP)",
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
            if "unusual" in lower or "activity" in lower:
                vi_msg = "IP bị đánh dấu hoạt động bất thường (captcha score thấp)"
            else:
                vi_msg = "Không có quyền truy cập"
        elif status_code == 400:
            if "invalid value" in lower and "aspect_ratio" in lower:
                vi_msg = "aspectRatio không hợp lệ. Dùng VIDEO_ASPECT_RATIO_LANDSCAPE hoặc VIDEO_ASPECT_RATIO_PORTRAIT"
            elif "unknown name" in lower:
                vi_msg = f"Payload chứa field không hợp lệ: {text[:200]}"
            else:
                vi_msg = f"Request không hợp lệ: {text[:200]}"
        elif "upload" in lower:
            vi_msg = "Lỗi upload media"
        elif "video" in lower:
            vi_msg = "Lỗi tạo video"
        elif "image" in lower:
            vi_msg = "Lỗi tạo ảnh"
        else:
            vi_msg = f"Lỗi tạo nội dung ({status_code})"

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

    # Log outgoing payload summary (model name, num imageInputs)
    if body and "batchGenerateImages" in url:
        try:
            req_item = body.get("requests", [{}])[0]
            model = req_item.get("imageModelName", "?")
            inputs_count = len(req_item.get("imageInputs", []))
            aspect = req_item.get("imageAspectRatio", "?")
            logger.info(f"{tag} → batchGenerateImages payload: model={model} inputs={inputs_count} aspect={aspect}")
        except Exception:
            pass

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
                        raise HTTPException(resp.status, detail={
                            "success": False,
                            "error": reason,
                            "message": vi_msg,
                            "statusCode": resp.status,
                        })
                    if is_retryable and attempt < FLOW_MAX_RETRIES:
                        logger.warning(
                            f"{tag} Flow API {resp.status} [{reason}] (lần {attempt}/{FLOW_MAX_RETRIES}): {vi_msg}. "
                            f"Thử lại sau {FLOW_RETRY_DELAY}s..."
                        )
                        await asyncio.sleep(FLOW_RETRY_DELAY)
                        continue
                    raise HTTPException(resp.status, detail={
                        "success": False,
                        "error": reason,
                        "message": vi_msg,
                        "statusCode": resp.status,
                    })
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
    logger.debug(f"Uploaded reference image: {media_id}")
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


UPLOAD_VIDEO_BASE = "https://aisandbox-pa.sandbox.googleapis.com/upload/v1/flow/upload/video"


async def _upload_video(
    access_token: str,
    project_id: str,
    video_bytes: bytes,
    mime_type: str = "video/mp4",
) -> dict:
    """Upload video via Google resumable upload.

    Step 1: POST initiate → get resumable sessionUrl
    Step 2: POST binary with upload,finalize → get mediaServerId
    Returns {mediaServerId, workflowServerId, videoWidth, videoHeight}
    """
    tag = f"[{project_id[:8]}]"
    connector = None
    proxy_entry = proxy_pool.next()
    if proxy_entry and ProxyConnector:
        connector = ProxyConnector.from_url(proxy_entry.url)

    common_headers = {
        "Authorization": f"Bearer {access_token}",
        "Origin": "https://labs.google",
        "Referer": "https://labs.google/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
    }

    async with aiohttp.ClientSession(connector=connector) as session:
        # Step 1: Initiate resumable upload
        init_url = f"{UPLOAD_VIDEO_BASE}/{project_id}?upload_protocol=resumable"
        init_headers = {
            **common_headers,
            "Content-Type": "application/json",
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Type": mime_type,
            "X-Goog-Upload-Header-Content-Length": str(len(video_bytes)),
        }
        async with session.post(
            init_url,
            headers=init_headers,
            json={},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status not in (200, 308):
                text = await resp.text()
                logger.error(f"{tag} Upload video init failed: {resp.status} {text[:300]}")
                raise HTTPException(resp.status, f"Upload video init failed: {resp.status}")
            session_url = resp.headers.get("X-Goog-Upload-URL") or resp.headers.get("Location")
            if not session_url:
                # Fallback: check JSON body
                try:
                    data = await resp.json()
                    session_url = data.get("sessionUrl")
                except Exception:
                    pass
            if not session_url:
                raise HTTPException(500, "Upload video init: no session URL in response")
            logger.info(f"{tag} Upload video session started, size={len(video_bytes)}")

        # Step 2: Upload binary to sessionUrl (POST with Google upload headers)
        upload_headers = {
            **common_headers,
            "Content-Type": mime_type,
            "X-Goog-Upload-Command": "upload, finalize",
            "X-Goog-Upload-Offset": "0",
        }
        async with session.post(
            session_url,
            headers=upload_headers,
            data=video_bytes,
            timeout=aiohttp.ClientTimeout(total=300),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.error(f"{tag} Upload video failed: {resp.status} {text[:300]}")
                raise HTTPException(resp.status, f"Upload video failed: {resp.status}")
            upload_data = await resp.json()
            media_id = (
                upload_data.get("mediaId")
                or upload_data.get("mediaServerId")
                or upload_data.get("media", {}).get("name")
            )
            if not media_id:
                logger.error(f"{tag} Upload video: no mediaId in response: {str(upload_data)[:300]}")
                raise HTTPException(500, detail={
                    "success": False, "error": "VIDEO_UPLOAD_NO_ID",
                    "message": "Upload video thành công nhưng không nhận được mediaId từ Google.",
                    "statusCode": 500,
                })
            upload_data["mediaId"] = media_id
            logger.info(
                f"{tag} Video uploaded: mediaId={media_id} "
                f"w={upload_data.get('videoWidth')} h={upload_data.get('videoHeight')}"
            )
            return upload_data


# ---------------------------------------------------------------------------
# Image endpoints
# ---------------------------------------------------------------------------

@router.post("/images/generate")
async def generate_image(req: ImageGenerateRequest):
    """Generate image (T2I). Include referenceImages or referenceImageBase64 for I2I."""
    project_id, token = _pick_session(req.projectId, req.accessToken)
    image_inputs: list = []
    upload_tasks: list = []

    if req.referenceImages:
        for ref in req.referenceImages:
            upload_tasks.append(
                _upload_reference_image(
                    token, project_id,
                    ref.base64, ref.mimeType, ref.fileName,
                )
            )
    elif req.referenceImageBase64:
        upload_tasks.append(
            _upload_reference_image(
                token, project_id,
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
    ctx = _client_context(project_id, recaptcha)
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
        f"{FLOW_API_BASE}/projects/{project_id}/flowMedia:batchGenerateImages",
        token,
        body,
        project_id=project_id,
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


UPSCALE_MAX_RETRIES = 3
UPSCALE_RETRY_DELAY = 8


@router.post("/images/generate-v2")
async def generate_image_v2(req: ImageGenerateRequest):
    """Generate image V2 — matches current web UI payload.

    Default imageModel changed to NARWHAL (vs GEM_PIX_2 in V1).
    Same payload structure as V1 (already matches).
    """
    project_id, token = _pick_session(req.projectId, req.accessToken)
    image_inputs: list = []
    upload_tasks: list = []

    if req.referenceImages:
        for ref in req.referenceImages:
            upload_tasks.append(
                _upload_reference_image(
                    token, project_id,
                    ref.base64, ref.mimeType, ref.fileName,
                )
            )
    elif req.referenceImageBase64:
        upload_tasks.append(
            _upload_reference_image(
                token, project_id,
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
    ctx = _client_context(project_id, recaptcha)
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
        f"{FLOW_API_BASE}/projects/{project_id}/flowMedia:batchGenerateImages",
        token,
        body,
        project_id=project_id,
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
    project_id, token = _pick_session(req.projectId, req.accessToken)
    tag = f"[{project_id[:8]}]"

    resolution = req.targetResolution
    if resolution and not resolution.startswith("UPSAMPLE_IMAGE_"):
        resolution = f"UPSAMPLE_IMAGE_{resolution}"

    last_err: Optional[HTTPException] = None
    for attempt in range(1, UPSCALE_MAX_RETRIES + 1):
        recaptcha = await _mint_recaptcha("IMAGE_GENERATION")
        ctx = _client_context(project_id, recaptcha, paygate_tier="PAYGATE_TIER_TWO")
        body = {
            "mediaId": req.mediaId,
            "targetResolution": resolution,
            "clientContext": ctx,
        }
        try:
            result = await _flow_request(
                "POST",
                f"{FLOW_API_BASE}/flow/upsampleImage",
                token,
                body,
                timeout_s=300,
                project_id=project_id,
            )
            if attempt > 1:
                logger.info(f"{tag} Upscale OK after {attempt} attempts")
            encoded = result.get("encodedImage", "")
            return {
                "success": True,
                "batchId": None,
                "media": [{
                    "mediaName": None,
                    "mediaId": req.mediaId,
                    "fifeUrl": None,
                    "encodedImage": encoded,
                    "targetResolution": req.targetResolution,
                    "seed": None,
                    "prompt": None,
                    "aspectRatio": None,
                    "dimensions": None,
                }],
                "remainingCredits": result.get("remainingCredits"),
                "encodedImage": encoded,
            }
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

@router.post("/videos/upload")
async def upload_video(req: VideoUploadRequest):
    """Upload a video file for use with Edit Video (abra_edit).

    Accepts base64-encoded video, uploads to Google via resumable upload.
    Returns mediaServerId to use as videoMediaId in /videos/edit.
    """
    project_id, token = _pick_session(req.projectId, req.accessToken)
    video_bytes = base64.b64decode(req.videoBase64)
    result = await _upload_video(token, project_id, video_bytes, req.mimeType)
    return {
        "success": True,
        "mediaServerId": result.get("mediaServerId"),
        "workflowServerId": result.get("workflowServerId"),
        "videoWidth": result.get("videoWidth"),
        "videoHeight": result.get("videoHeight"),
    }


@router.post("/videos/generate")
async def generate_video(req: VideoGenerateRequest):
    """Generate video. Modes:
    - T2V: no images, text prompt only
    - I2V: startImageBase64 only
    - I2V-FL: startImageBase64 + endImageBase64 (first-last frame)
    - R2V: referenceImages only (uses ReferenceImages endpoint)
    - Edit Video: referenceVideos + optional referenceImages (Omni models only)
    """
    project_id, token = _pick_session(req.projectId, req.accessToken)

    has_images = bool(req.referenceImages)
    has_videos = bool(req.referenceVideos)
    has_refs = has_images or has_videos
    is_edit = has_videos

    if not req.promptText.strip():
        raise HTTPException(400, detail={
            "success": False, "error": "INVALID_PROMPT",
            "message": "promptText không được để trống", "statusCode": 400,
        })

    if has_videos and req.videoModelKey not in OMNI_MODELS:
        raise HTTPException(400, detail={
            "success": False, "error": "MODEL_NOT_SUPPORTED",
            "message": (
                f"referenceVideos chỉ hỗ trợ Omni models: {', '.join(OMNI_MODELS)}. "
                f"Model hiện tại '{req.videoModelKey}' không hỗ trợ edit video. "
                f"Đổi videoModelKey sang 'abra_edit' hoặc bỏ referenceVideos."
            ),
            "statusCode": 400,
        })
    if has_videos and len(req.referenceVideos) > 1:
        raise HTTPException(400, detail={
            "success": False, "error": "TOO_MANY_VIDEOS",
            "message": "Chỉ hỗ trợ tối đa 1 video reference. Gửi 1 video trong referenceVideos.",
            "statusCode": 400,
        })

    # --- Upload references ---
    upload_tasks: list = []
    start_media_id: Optional[str] = None
    end_media_id: Optional[str] = None
    ref_image_media_ids: list = []
    video_media_id: Optional[str] = None

    if has_refs:
        img_upload_tasks = [
            _upload_user_image(token, ref.base64, ref.aspectRatio, ref.mimeType)
            for ref in (req.referenceImages or [])
        ]
        vid_upload_tasks = []
        if has_videos:
            vid = req.referenceVideos[0]
            try:
                vid_bytes = base64.b64decode(vid.base64)
            except Exception as e:
                raise HTTPException(400, detail={
                    "success": False, "error": "INVALID_VIDEO_BASE64",
                    "message": f"Không thể decode base64 video: {e}",
                    "statusCode": 400,
                })
            if len(vid_bytes) < 1000:
                raise HTTPException(400, detail={
                    "success": False, "error": "VIDEO_TOO_SMALL",
                    "message": f"Video quá nhỏ ({len(vid_bytes)} bytes). Kiểm tra lại base64 data.",
                    "statusCode": 400,
                })
            vid_upload_tasks.append(
                _upload_video(token, project_id, vid_bytes, vid.mimeType)
            )

        all_results = await asyncio.gather(*img_upload_tasks, *vid_upload_tasks)
        ref_image_media_ids = list(all_results[:len(img_upload_tasks)])
        if vid_upload_tasks:
            vid_result = all_results[len(img_upload_tasks)]
            video_media_id = vid_result.get("mediaId") or vid_result.get("mediaServerId")
            if not video_media_id:
                raise HTTPException(500, detail={
                    "success": False, "error": "VIDEO_UPLOAD_NO_ID",
                    "message": "Upload video thành công nhưng không nhận được mediaId từ Google.",
                    "statusCode": 500,
                })
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
            idx = 0
            if req.startImageBase64:
                start_media_id = uploaded[idx]
                idx += 1
            if req.endImageBase64:
                end_media_id = uploaded[idx]

    # --- Mint reCAPTCHA ---
    recaptcha = await _mint_recaptcha("VIDEO_GENERATION")
    ctx = _client_context(project_id, recaptcha, paygate_tier="PAYGATE_TIER_TWO")
    seed = req.seed if req.seed is not None else int(time.time() * 1000) % 1000000
    batch_id = str(uuid.uuid4())

    if is_edit:
        # Edit Video (Omni): video + optional image references
        req_item: dict = {
            "aspectRatio": req.aspectRatio,
            "metadata": {},
            "referenceImages": [
                {"mediaId": mid, "imageUsageType": "IMAGE_USAGE_TYPE_ASSET"}
                for mid in ref_image_media_ids
            ],
            "seed": seed,
            "textInput": {
                "structuredPrompt": {"parts": [{"text": req.promptText}]},
            },
            "videoInput": {
                "mediaId": video_media_id,
                "startFrameIndex": req.startFrameIndex,
                "endFrameIndex": req.endFrameIndex,
            },
            "videoModelKey": req.videoModelKey,
        }
        if req.referenceAudio:
            req_item["referenceAudio"] = [{"mediaId": req.referenceAudio}]
        endpoint = f"{FLOW_API_BASE}/video:batchAsyncGenerateVideoEditVideo"
        body = {
            "clientContext": ctx,
            "mediaGenerationContext": {
                "batchId": batch_id,
                "audioFailurePreference": "BLOCK_SILENCED_VIDEOS",
            },
            "requests": [req_item],
        }
    elif has_refs:
        # R2V: image references only
        req_item = {
            "aspectRatio": req.aspectRatio,
            "seed": seed,
            "textInput": {
                "structuredPrompt": {"parts": [{"text": req.promptText}]},
            },
            "videoModelKey": req.videoModelKey,
            "metadata": {},
            "referenceImages": [
                {"mediaId": mid, "imageUsageType": "IMAGE_USAGE_TYPE_ASSET"}
                for mid in ref_image_media_ids
            ],
        }
        if req.referenceAudio:
            req_item["referenceAudio"] = [{"mediaId": req.referenceAudio}]
        endpoint = f"{FLOW_API_BASE}/video:batchAsyncGenerateVideoReferenceImages"
        body = {
            "mediaGenerationContext": {"batchId": batch_id},
            "clientContext": ctx,
            "requests": [req_item],
            "useV2ModelConfig": True,
        }
    else:
        # T2V / I2V / I2V-FL
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

    result = await _flow_request("POST", endpoint, token, body, project_id=project_id)

    operations = _parse_video_response(result)

    return {
        "success": True,
        "batchId": batch_id,
        "operations": operations,
        "remainingCredits": result.get("remainingCredits"),
    }


def _parse_video_response(result: dict) -> list:
    """Parse video generate response.

    Supports both old schema (operations[]) and new schema (media[] + workflows[]).
    New schema (current): each media item has name=mediaId, workflowId, mediaMetadata.mediaStatus.
    """
    operations = []
    # New schema: media[]
    for m in result.get("media", []):
        media_id = m.get("name")
        workflow_id = m.get("workflowId")
        meta = m.get("mediaMetadata", {})
        status = meta.get("mediaStatus", {}).get("mediaGenerationStatus", "")
        video_data = m.get("video", {}).get("generatedVideo", {})
        operations.append({
            "operationName": media_id,  # used for status polling
            "mediaId": media_id,
            "workflowId": workflow_id,
            "sceneId": "",
            "status": status,
            "model": video_data.get("model"),
            "seed": video_data.get("seed"),
            "aspectRatio": video_data.get("aspectRatio"),
            "length": m.get("video", {}).get("dimensions", {}).get("length"),
        })
    # Old schema fallback: operations[]
    if not operations:
        for op in result.get("operations", []):
            operations.append({
                "operationName": op.get("operation", {}).get("name"),
                "sceneId": op.get("sceneId"),
            })
    return operations


@router.post("/videos/generate-v2")
async def generate_video_v2(req: VideoGenerateRequest):
    """Generate video with V2 payload schema (matches current web UI).

    Differences from /videos/generate:
    - Uses `structuredPrompt: {parts: [{text}]}` instead of `prompt`
    - Adds `mediaGenerationContext.audioFailurePreference = "BLOCK_SILENCED_VIDEOS"`
    - Adds top-level `useV2ModelConfig: true` for all modes
    - Modes supported: T2V, I2V, I2V-FL, R2V
    """
    project_id, token = _pick_session(req.projectId, req.accessToken)
    is_r2v = bool(req.referenceImages)

    # --- Upload images ---
    upload_tasks: list = []
    start_media_id: Optional[str] = None
    end_media_id: Optional[str] = None
    ref_media_ids: list = []

    if is_r2v:
        for ref in req.referenceImages:
            upload_tasks.append(
                _upload_reference_image(token, project_id, ref.base64, ref.mimeType)
            )
    else:
        # I2V / I2V-FL: use flow/uploadImage → UUID mediaId (matches web UI)
        if req.startImageBase64:
            upload_tasks.append(
                _upload_reference_image(
                    token, project_id, req.startImageBase64, req.startImageMimeType,
                )
            )
        if req.endImageBase64:
            upload_tasks.append(
                _upload_reference_image(
                    token, project_id, req.endImageBase64, req.endImageMimeType,
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

    recaptcha = await _mint_recaptcha("VIDEO_GENERATION")
    ctx = _client_context(project_id, recaptcha, paygate_tier="PAYGATE_TIER_TWO")
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
        if req.referenceAudio:
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

    result = await _flow_request("POST", endpoint, token, body, project_id=project_id)

    operations = _parse_video_response(result)

    return {
        "success": True,
        "batchId": batch_id,
        "operations": operations,
        "remainingCredits": result.get("remainingCredits"),
    }


@router.post("/videos/edit")
async def edit_video(req: VideoEditRequest):
    """Deprecated: use /videos/generate with video in referenceImages instead.
    Kept for backward compatibility — accepts pre-uploaded videoMediaId.
    """
    project_id, token = _pick_session(req.projectId, req.accessToken)

    ref_media_ids: list = []
    if req.referenceImages:
        upload_tasks = [
            _upload_user_image(token, ref.base64, ref.aspectRatio, ref.mimeType)
            for ref in req.referenceImages
        ]
        ref_media_ids = list(await asyncio.gather(*upload_tasks))

    recaptcha = await _mint_recaptcha("VIDEO_GENERATION")
    ctx = _client_context(project_id, recaptcha, paygate_tier="PAYGATE_TIER_TWO")
    seed = req.seed if req.seed is not None else int(time.time() * 1000) % 1000000
    batch_id = str(uuid.uuid4())

    req_item: dict = {
        "aspectRatio": req.aspectRatio,
        "metadata": {},
        "referenceImages": [
            {"mediaId": mid, "imageUsageType": "IMAGE_USAGE_TYPE_ASSET"}
            for mid in ref_media_ids
        ],
        "seed": seed,
        "textInput": {
            "structuredPrompt": {"parts": [{"text": req.promptText}]},
        },
        "videoInput": {
            "mediaId": req.videoMediaId,
            "startFrameIndex": req.startFrameIndex,
            "endFrameIndex": req.endFrameIndex,
        },
        "videoModelKey": req.videoModelKey,
    }
    if req.referenceAudio:
        req_item["referenceAudio"] = [{"mediaId": req.referenceAudio}]

    body = {
        "clientContext": ctx,
        "mediaGenerationContext": {
            "batchId": batch_id,
            "audioFailurePreference": "BLOCK_SILENCED_VIDEOS",
        },
        "requests": [req_item],
    }

    result = await _flow_request(
        "POST",
        f"{FLOW_API_BASE}/video:batchAsyncGenerateVideoEditVideo",
        token, body, project_id=project_id,
    )
    operations = _parse_video_response(result)
    return {
        "success": True,
        "batchId": batch_id,
        "operations": operations,
        "remainingCredits": result.get("remainingCredits"),
    }


@router.post("/videos/upscale")
async def upscale_video(req: VideoUpscaleRequest):
    """Upscale video to 1080p/4K. Returns operation for polling."""
    project_id, token = _pick_session(req.projectId, req.accessToken)
    recaptcha = await _mint_recaptcha("VIDEO_GENERATION")
    ctx = _client_context(project_id, recaptcha, paygate_tier="PAYGATE_TIER_TWO")
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
        project_id=project_id,
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
