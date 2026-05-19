# Video Generation API

Tài liệu hướng dẫn tạo video qua FlowCaptchaPT backend.

## Endpoints

| Endpoint | Mô tả |
|----------|-------|
| `POST /api/captcha` | Mint reCAPTCHA token |
| `POST /api/flow/videos/generate` | Generate video V1 (T2V / I2V / I2V-FL / R2V) |
| `POST /api/flow/videos/generate-v2` | Generate video V2 (match web UI, khuyến nghị) |
| `POST /api/flow/videos/edit` | Edit Video — Omni Flash (`abra_edit`) |
| `POST /api/flow/videos/upscale` | Upscale video 1080p/4K |
| `POST /api/flow/videos/status` | Poll trạng thái video |

Base URL ví dụ:

- VPS2: `http://45.32.38.125:9339`

---

## 1. Authentication

Giống Image API — cần `accessToken` (Bearer OAuth) và `projectId` (UUID).

Hoặc register session 1 lần rồi dùng `projectId: "auto"`:

```http
POST /api/flow/sessions/register
Content-Type: application/json
```

```json
{
  "projectId": "c28340af-cd8d-4cfa-b2d5-aa3bb00fd616",
  "accessToken": "ya29.a0AQvPyI...",
  "cookies": ""
}
```

---

## 2. Video Models

### T2V / I2V models

| Model Key | Mô tả | Duration | Aspect Ratios |
|-----------|--------|----------|---------------|
| `veo_3_1_t2v_fast_4s` | Veo 3.1 T2V nhanh | 4s | 16:9, 9:16 |
| `veo_3_1_t2v_fast_8s` | Veo 3.1 T2V nhanh | 8s | 16:9, 9:16 |
| `veo_3_1_t2v_quality_8s` | Veo 3.1 T2V chất lượng | 8s | 16:9, 9:16 |
| `veo_3_1_i2v_s_fast_portrait_ultra` | Veo 3.1 I2V nhanh | 4s | 16:9, 9:16 |
| `veo_3_1_i2v_s_quality_portrait_ultra` | Veo 3.1 I2V chất lượng | 8s | 16:9, 9:16 |

### R2V models (Reference-to-Video)

| Model Key | Mô tả | Duration | Max refs |
|-----------|--------|----------|----------|
| `abra_r2v` | R2V cũ (legacy) | 4-8s | 1-3 ảnh |
| `abra_r2v_10s` | R2V mới — video 10 giây | 10s | 1-3 ảnh |

### Edit Video model

| Model Key | Mô tả | Input |
|-----------|--------|-------|
| `abra_edit` | Omni Flash — edit video | Video + prompt + optional refs |

### Upscale models

| Model Key | Mô tả |
|-----------|--------|
| `veo_3_1_upsampler_1080p` | Upscale lên 1080p |

### Aspect ratios

| Value | Mô tả |
|-------|--------|
| `VIDEO_ASPECT_RATIO_LANDSCAPE` | 16:9 ngang |
| `VIDEO_ASPECT_RATIO_PORTRAIT` | 9:16 dọc |

---

## 3. T2V — Text to Video

### Request

```http
POST /api/flow/videos/generate-v2
Content-Type: application/json
```

```json
{
  "projectId": "auto",
  "promptText": "a cinematic sunset over the ocean with gentle waves",
  "aspectRatio": "VIDEO_ASPECT_RATIO_LANDSCAPE",
  "videoModelKey": "veo_3_1_t2v_fast_4s"
}
```

### Response

```json
{
  "success": true,
  "batchId": "bd5cfc7b-940f-4c92-a236-bc631a7e2925",
  "operations": [
    {
      "operationName": "32e6bbea-2a49-402b-9056-59b11e02115c",
      "mediaId": "32e6bbea-2a49-402b-9056-59b11e02115c",
      "workflowId": "546be7ab-d6a0-4bae-9b17-d0fbebad36a5",
      "sceneId": "",
      "status": "MEDIA_GENERATION_STATUS_SCHEDULED",
      "model": "veo_3_1_t2v_fast_4s",
      "seed": 138341,
      "aspectRatio": "VIDEO_ASPECT_RATIO_LANDSCAPE",
      "length": "4s"
    }
  ],
  "remainingCredits": 21200
}
```

---

## 4. I2V — Image to Video

### Start frame only

```json
{
  "projectId": "auto",
  "promptText": "camera slowly zooms into the scene",
  "aspectRatio": "VIDEO_ASPECT_RATIO_LANDSCAPE",
  "videoModelKey": "veo_3_1_i2v_s_fast_portrait_ultra",
  "startImageBase64": "/9j/4AAQSkZJRgABAQ...",
  "startImageMimeType": "image/jpeg",
  "startImageAspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE"
}
```

### First + Last frame (I2V-FL)

```json
{
  "projectId": "auto",
  "promptText": "smooth transition between two scenes",
  "aspectRatio": "VIDEO_ASPECT_RATIO_LANDSCAPE",
  "videoModelKey": "veo_3_1_i2v_s_quality_portrait_ultra",
  "startImageBase64": "/9j/4AAQ...",
  "startImageMimeType": "image/jpeg",
  "startImageAspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE",
  "endImageBase64": "/9j/4AAQ...",
  "endImageMimeType": "image/jpeg",
  "endImageAspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE"
}
```

---

## 5. R2V — Reference Images to Video

Dùng reference images (1-3 ảnh) để tạo video. Có 2 model:

### R2V cũ (legacy) — `abra_r2v`

```json
{
  "projectId": "auto",
  "promptText": "cinematic sunset over the ocean with gentle waves",
  "aspectRatio": "VIDEO_ASPECT_RATIO_LANDSCAPE",
  "videoModelKey": "abra_r2v",
  "referenceImages": [
    {"base64": "/9j/4AAQ...", "mimeType": "image/jpeg", "aspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE"}
  ],
  "referenceAudio": "zephyr"
}
```

### R2V mới — `abra_r2v_10s` (10 giây, khuyến nghị)

```json
{
  "projectId": "auto",
  "promptText": "cinematic sunset over the ocean with gentle waves, smooth camera movement",
  "aspectRatio": "VIDEO_ASPECT_RATIO_LANDSCAPE",
  "videoModelKey": "abra_r2v_10s",
  "referenceImages": [
    {"base64": "/9j/4AAQ...", "mimeType": "image/jpeg", "aspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE"},
    {"base64": "/9j/4AAQ...", "mimeType": "image/jpeg", "aspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE"}
  ],
  "referenceAudio": "zephyr"
}
```

### So sánh R2V models

| | `abra_r2v` (cũ) | `abra_r2v_10s` (mới) |
|---|---|---|
| Duration | 4-8s | **10s** |
| Max references | 1-3 ảnh | 1-3 ảnh |
| Endpoint | `generate-v2` | `generate-v2` |
| Payload | giống nhau | giống nhau |
| `useV2ModelConfig` | ✅ | ✅ |
| `referenceAudio` | zephyr | zephyr |

Chỉ cần đổi `videoModelKey` — mọi thứ khác giữ nguyên.

### R2V fields

| Field | Type | Default | Mô tả |
|-------|------|---------|-------|
| `referenceImages` | array | required | 1-3 ảnh reference |
| `referenceImages[].base64` | string | required | Base64 JPEG/PNG |
| `referenceImages[].mimeType` | string | `image/jpeg` | MIME type |
| `referenceImages[].aspectRatio` | string | `IMAGE_ASPECT_RATIO_LANDSCAPE` | Aspect ratio ảnh |
| `referenceAudio` | string | `zephyr` | Audio preset |

---

## 6. Edit Video — Omni Flash (`abra_edit`)

Edit video đã có sẵn bằng prompt + optional reference images.

### Request

```http
POST /api/flow/videos/edit
Content-Type: application/json
```

```json
{
  "projectId": "auto",
  "promptText": "make the sky purple and add northern lights",
  "videoMediaId": "32e6bbea-2a49-402b-9056-59b11e02115c",
  "startFrameIndex": 0,
  "endFrameIndex": 240,
  "aspectRatio": "VIDEO_ASPECT_RATIO_LANDSCAPE",
  "videoModelKey": "abra_edit"
}
```

### Với reference images

```json
{
  "projectId": "auto",
  "promptText": "apply the style from reference images to the video",
  "videoMediaId": "32e6bbea-2a49-402b-9056-59b11e02115c",
  "startFrameIndex": 0,
  "endFrameIndex": 240,
  "videoModelKey": "abra_edit",
  "referenceImages": [
    {"base64": "/9j/4AAQ...", "mimeType": "image/jpeg", "aspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE"}
  ],
  "referenceAudio": "zephyr"
}
```

### Edit Video fields

| Field | Type | Default | Mô tả |
|-------|------|---------|-------|
| `videoMediaId` | string | required | mediaId của video gốc |
| `startFrameIndex` | int | `0` | Frame bắt đầu |
| `endFrameIndex` | int | `240` | Frame kết thúc |
| `promptText` | string | required | Hướng dẫn edit |
| `videoModelKey` | string | `abra_edit` | Omni Flash model |
| `referenceImages` | array | optional | 1-3 ảnh style reference |
| `referenceAudio` | string | `zephyr` | Audio preset |

---

## 7. Poll Video Status

Video generation là async — cần poll status cho đến khi hoàn thành.

### Request

```http
POST /api/flow/videos/status
Content-Type: application/json
```

```json
{
  "accessToken": "ya29.a0AQvPyI...",
  "operations": [
    {"operationName": "32e6bbea-2a49-402b-9056-59b11e02115c", "sceneId": ""}
  ]
}
```

### Response (đang tạo)

```json
{
  "success": true,
  "completed": false,
  "operations": [
    {
      "status": "MEDIA_GENERATION_STATUS_ACTIVE",
      "operationName": "32e6bbea-2a49-402b-9056-59b11e02115c",
      "sceneId": "",
      "mediaGenerationId": "..."
    }
  ]
}
```

### Response (hoàn thành)

```json
{
  "success": true,
  "completed": true,
  "operations": [
    {
      "status": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
      "operationName": "f9b1e7ab-66b2-4c41-bd07-e8614deed25a",
      "sceneId": "",
      "mediaGenerationId": "CAUSJGMy...",
      "video": {
        "fifeUrl": "https://flow-content.google/video/f9b1e7ab-...?Expires=...&Signature=...",
        "servingBaseUri": "https://flow-content.google/image/f9b1e7ab-...?Expires=...",
        "mediaId": "f9b1e7ab-66b2-4c41-bd07-e8614deed25a",
        "seed": 677192,
        "prompt": "cinematic sunset over the ocean...",
        "model": "abra_r2v_10s",
        "aspectRatio": "VIDEO_ASPECT_RATIO_LANDSCAPE"
      }
    }
  ],
  "remainingCredits": 21170
}
```

### Video status values

| Status | Mô tả |
|--------|-------|
| `MEDIA_GENERATION_STATUS_SCHEDULED` | Đã nhận, đang chờ |
| `MEDIA_GENERATION_STATUS_PENDING` | Đang chờ xử lý |
| `MEDIA_GENERATION_STATUS_ACTIVE` | Đang render |
| `MEDIA_GENERATION_STATUS_SUCCESSFUL` | Thành công — có `video.fifeUrl` |
| `MEDIA_GENERATION_STATUS_FAILED` | Thất bại — retry hoặc đổi prompt |

Download video từ `video.fifeUrl` (CDN URL có signature, expire sau ~48h).

---

## 8. Upscale Video

### Request

```http
POST /api/flow/videos/upscale
Content-Type: application/json
```

```json
{
  "accessToken": "ya29.a0AQvPyI...",
  "projectId": "c28340af-...",
  "videoMediaId": "f9b1e7ab-66b2-4c41-bd07-e8614deed25a",
  "resolution": "VIDEO_RESOLUTION_1080P",
  "aspectRatio": "VIDEO_ASPECT_RATIO_LANDSCAPE",
  "videoModelKey": "veo_3_1_upsampler_1080p"
}
```

### Response

Trả về `operations[]` — poll bằng `/videos/status` như bình thường.

---

## 9. Full example — R2V 10s (Python)

```python
import json, base64, time, urllib.request

VPS = "http://45.32.38.125:9339"
PROJECT_ID = "c28340af-cd8d-4cfa-b2d5-aa3bb00fd616"

# Load image
with open("photo.jpg", "rb") as f:
    img_b64 = base64.b64encode(f.read()).decode()

# 1. Generate R2V 10s
body = {
    "projectId": PROJECT_ID,
    "promptText": "cinematic sunset over the ocean with gentle waves",
    "aspectRatio": "VIDEO_ASPECT_RATIO_LANDSCAPE",
    "videoModelKey": "abra_r2v_10s",
    "referenceImages": [
        {"base64": img_b64, "mimeType": "image/jpeg", "aspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE"}
    ],
    "referenceAudio": "zephyr",
}

req = urllib.request.Request(
    f"{VPS}/api/flow/videos/generate-v2",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=180) as resp:
    result = json.loads(resp.read())

op_name = result["operations"][0]["operationName"]
print(f"Operation: {op_name}")
print(f"Model: {result['operations'][0]['model']}")
print(f"Length: {result['operations'][0]['length']}")

# 2. Poll status
while True:
    time.sleep(15)
    status_body = {
        "accessToken": "ya29...",  # or use session
        "operations": [{"operationName": op_name, "sceneId": ""}],
    }
    req = urllib.request.Request(
        f"{VPS}/api/flow/videos/status",
        data=json.dumps(status_body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        status = json.loads(resp.read())

    op = status["operations"][0]
    print(f"Status: {op['status']}")

    if status["completed"]:
        if op["status"] == "MEDIA_GENERATION_STATUS_SUCCESSFUL":
            print(f"Video URL: {op['video']['fifeUrl']}")
        else:
            print(f"Failed: {op['status']}")
        break
```

---

## 10. Full example — Edit Video (Python)

```python
import json, urllib.request

VPS = "http://45.32.38.125:9339"

body = {
    "projectId": "auto",
    "promptText": "make the sky purple and add northern lights",
    "videoMediaId": "f9b1e7ab-66b2-4c41-bd07-e8614deed25a",
    "startFrameIndex": 0,
    "endFrameIndex": 240,
    "videoModelKey": "abra_edit",
}

req = urllib.request.Request(
    f"{VPS}/api/flow/videos/edit",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=180) as resp:
    result = json.loads(resp.read())

print(f"Operation: {result['operations'][0]['operationName']}")
# Poll status same as R2V example above
```

---

## 11. Error handling

| HTTP | Detail | Action |
|------|--------|--------|
| 200 | success: true | OK |
| 401 | Token hết hạn | Refresh access token |
| 403 | UNUSUAL_ACTIVITY | Captcha score thấp — retry sau 30-60s |
| 403 | Vi phạm chính sách | Content moderation — đổi prompt |
| 429 | TOO_MUCH_TRAFFIC | Rate limit — chờ 20s+ |
| 500 | Lỗi tạo nội dung | Check payload format |
| 503 | reCAPTCHA mint timeout | Chrome hang, restart backend |

Retry strategy:
- **403 UNUSUAL_ACTIVITY**: retry với captcha mới sau 30s
- **429 TOO_MUCH_TRAFFIC**: chờ 60s+, không retry
- **MEDIA_GENERATION_STATUS_FAILED**: tạo lại (max 3 lần)
- **503 transient**: retry 3 lần với delay 10s

---

## 12. Timing benchmarks

| Operation | Avg time |
|-----------|----------|
| Mint captcha | 5-15s |
| T2V 4s | 30-50s (mint + generate) |
| T2V 8s | 40-70s |
| I2V | 35-60s |
| R2V `abra_r2v` | 40-70s |
| R2V `abra_r2v_10s` | 50-90s |
| Edit Video `abra_edit` | 40-80s |
| Upscale 1080p | 60-120s |
| Poll status | 1-2s per call |

---

## 13. Best practices

1. **Poll interval**: 15s cho lần đầu, 10s các lần sau
2. **R2V model**: dùng `abra_r2v_10s` cho video 10s, `abra_r2v` nếu cần ngắn hơn
3. **Edit Video**: cần `videoMediaId` từ video đã tạo trước đó
4. **Delay 15-20s** giữa các requests cùng project (tránh 429)
5. **Reuse captcha không khả thi** — mỗi Flow API call cần token mới
6. **Download `fifeUrl` ngay** khi nhận — URL expire sau ~48h
7. **Handle FAILED status** bằng retry (max 3), đổi seed mỗi lần
8. **referenceAudio**: mặc định `"zephyr"`, truyền theo payload Google
