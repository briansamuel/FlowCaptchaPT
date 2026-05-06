"""Flow API endpoints - Image/Video generation via Google Labs Flow."""
from __future__ import annotations
import asyncio
import json
import logging
import time
import uuid
from typing import Optional, List

import aiohttp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..captcha.service import get_captcha_service
from ..captcha.queue import job_queue, JobStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/flow", tags=["flow"])

FLOW_API_BASE = "https://aisandbox-pa.googleapis.com/v1"


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _mint_recaptcha(action: str) -> str:
    service = get_captcha_service()
    try:
        result = await asyncio.wait_for(service.get_token(action), timeout=120)
    except asyncio.TimeoutError:
        raise HTTPException(503, "reCAPTCHA mint timeout (120s)")

    if result.token:
        return result.token

    if result.error and "Cooldown" in result.error:
        job = job_queue.submit(action, None, None)
        asyncio.ensure_future(job_queue.run_worker(job, service))
        deadline = time.time() + 120
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


def _headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Goog-AuthUser": "0",
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

    is_retryable = (
        ("unusual" in text.lower() and "too_much_traffic" not in text.lower())
        or status_code == 503
    )
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
) -> dict:
    async with aiohttp.ClientSession() as session:
        for attempt in range(1, FLOW_MAX_RETRIES + 1):
            try:
                async with session.request(
                    method,
                    url,
                    headers=_headers(access_token),
                    json=body,
                    timeout=aiohttp.ClientTimeout(total=timeout_s),
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    text = await resp.text()
                    reason, vi_msg, is_retryable = _parse_flow_error(text, resp.status)
                    if reason == "PUBLIC_ERROR_UNUSUAL_ACTIVITY_TOO_MUCH_TRAFFIC":
                        logger.warning(f"Flow API [{reason}]: {vi_msg}. Delay 20s...")
                        await asyncio.sleep(20)
                        raise HTTPException(resp.status, vi_msg)
                    if is_retryable and attempt < FLOW_MAX_RETRIES:
                        logger.warning(
                            f"Flow API {resp.status} [{reason}] (lần {attempt}/{FLOW_MAX_RETRIES}): {vi_msg}. "
                            f"Thử lại sau {FLOW_RETRY_DELAY}s..."
                        )
                        await asyncio.sleep(FLOW_RETRY_DELAY)
                        continue
                    logger.error(f"Flow API {resp.status} [{reason}]: {vi_msg}")
                    raise HTTPException(resp.status, vi_msg)
            except (asyncio.TimeoutError, aiohttp.ClientError) as e:
                if attempt < FLOW_MAX_RETRIES:
                    logger.warning(f"Flow API connection error (lần {attempt}/{FLOW_MAX_RETRIES}): {e}. Thử lại...")
                    await asyncio.sleep(FLOW_RETRY_DELAY)
                    continue
                logger.error(f"Flow API connection error: {e}")
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
        file_name = f"upload_{uuid.uuid4().hex[:8]}.jpg"
    body = {
        "clientContext": {"projectId": project_id, "tool": "PINHOLE"},
        "fileName": file_name,
        "imageBytes": image_b64,
        "isHidden": False,
        "isUserUploaded": True,
        "mimeType": mime_type,
    }
    result = await _flow_request(
        "POST", f"{FLOW_API_BASE}/flow/uploadImage", access_token, body,
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
    image_inputs: list = []
    upload_tasks: list = []

    if req.referenceImages:
        for ref in req.referenceImages:
            upload_tasks.append(
                _upload_reference_image(
                    req.accessToken, req.projectId,
                    ref.base64, ref.mimeType, ref.fileName,
                )
            )
    elif req.referenceImageBase64:
        upload_tasks.append(
            _upload_reference_image(
                req.accessToken, req.projectId,
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
        req.accessToken,
        body,
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
    """Upscale image to 2K/4K. Returns base64 JPEG."""
    recaptcha = await _mint_recaptcha("IMAGE_GENERATION")
    ctx = _client_context(req.projectId, recaptcha, paygate_tier="PAYGATE_TIER_TWO")

    body = {
        "mediaId": req.mediaId,
        "targetResolution": req.targetResolution,
        "clientContext": ctx,
    }

    result = await _flow_request(
        "POST",
        f"{FLOW_API_BASE}/flow/upsampleImage",
        req.accessToken,
        body,
        timeout_s=300,
    )

    return {"success": True, "encodedImage": result.get("encodedImage")}


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
                    req.accessToken, ref.base64, ref.aspectRatio, ref.mimeType,
                )
            )
    else:
        if req.startImageBase64:
            upload_tasks.append(
                _upload_user_image(
                    req.accessToken, req.startImageBase64,
                    req.startImageAspectRatio, req.startImageMimeType,
                )
            )
        if req.endImageBase64:
            upload_tasks.append(
                _upload_user_image(
                    req.accessToken, req.endImageBase64,
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

    result = await _flow_request("POST", endpoint, req.accessToken, body)

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
        req.accessToken,
        body,
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
