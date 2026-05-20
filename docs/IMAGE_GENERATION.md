# Image Generation API

Tài liệu hướng dẫn tạo ảnh qua FlowCaptchaPT backend.

## Endpoints

| Endpoint | Mô tả |
|----------|-------|
| `POST /api/captcha` | Mint reCAPTCHA token |
| `POST /api/flow/images/generate` | Generate image V1 (default model `GEM_PIX_2`) |
| `POST /api/flow/images/generate-v2` | Generate image V2 (default `NARWHAL`, match web UI) |
| `POST /api/flow/images/upscale` | Upscale image 2K/4K |

Base URL ví dụ:

- VPS2: `http://45.32.38.125:9339`


---

## 1. Authentication

Cần 2 thứ:
- **`accessToken`**: Bearer OAuth token từ Google account (lấy từ `labs.google/fx/api/auth/session`)
- **`projectId`**: UUID project Google Labs Flow

Token expire sau ~1h. Khi 401 trả về `Token xác thực hết hạn hoặc không hợp lệ` → cần refresh.

---

## 2. Generate Image — V2 (khuyến nghị)

### Request

```http
POST /api/flow/images/generate-v2
Content-Type: application/json
```

```json
{
  "accessToken": "ya29.a0AQvPyI...",
  "projectId": "370bdaea-7fa0-4834-8c97-70b2592cf14f",
  "promptText": "majestic dragon flying over mountain peaks at sunset",
  "aspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE",
  "imageModel": "NARWHAL",
  "seed": 12345
}
```

### Optional fields

| Field | Type | Default | Mô tả |
|-------|------|---------|-------|
| `accessToken` | string | required | OAuth Bearer token |
| `projectId` | string | required | Google project UUID |
| `promptText` | string | required | Prompt mô tả ảnh |
| `aspectRatio` | string | `IMAGE_ASPECT_RATIO_SQUARE` | xem bảng dưới |
| `imageModel` | string | `NARWHAL` (V2) / `GEM_PIX_2` (V1) | xem bảng dưới |
| `seed` | int | random | Seed reproducibility |
| `referenceImageBase64` | string | - | I2I — 1 reference image (base64) |
| `referenceImageMimeType` | string | `image/jpeg` | MIME của reference |
| `referenceImages` | array | - | I2I — nhiều reference (xem dưới) |

### Aspect ratio

| Value | Output dimensions (NARWHAL/R2I) |
|-------|--------|
| `IMAGE_ASPECT_RATIO_SQUARE` | 1024×1024 |
| `IMAGE_ASPECT_RATIO_LANDSCAPE` | 1376×768 (NARWHAL) / 1408×768 (R2I) |
| `IMAGE_ASPECT_RATIO_PORTRAIT` | 768×1376 (NARWHAL) / 768×1408 (R2I) |

### Image models

| Model | Use case | Output (16:9) |
|-------|----------|---------------|
| `NARWHAL` | T2I (text only) | 1376×768 |
| `R2I` | I2I (có reference) | 1408×768 |
| `GEM_PIX_2` | Legacy, V1 default | 1376×768 |

Auto-pick model:
```python
model = "R2I" if has_reference_image else "NARWHAL"
```

### Response

```json
{
  "success": true,
  "batchId": "cb411b10-f364-4e61-9f1c-cb72772e3494",
  "media": [
    {
      "mediaName": "16333573-64d3-4189-ad1c-74f198939ddb",
      "mediaId": "16333573-64d3-4189-ad1c-74f198939ddb",
      "fifeUrl": "https://flow-content.google/image/16333573-...?Expires=...&Signature=...",
      "seed": 378187,
      "prompt": "majestic dragon flying over mountain peaks at sunset",
      "aspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE",
      "dimensions": {"width": 1376, "height": 768}
    }
  ],
  "remainingCredits": 20405
}
```

Download ảnh từ `fifeUrl` (CDN URL có signature, expire sau ~48h).

---

## 3. I2I — Image to Image

### Single reference

```json
{
  "accessToken": "...",
  "projectId": "...",
  "promptText": "transform into anime style",
  "aspectRatio": "IMAGE_ASPECT_RATIO_PORTRAIT",
  "imageModel": "R2I",
  "referenceImageBase64": "/9j/4AAQSkZJRgABAQ...",
  "referenceImageMimeType": "image/jpeg"
}
```

### Multiple references (R2I support 1-3)

```json
{
  "accessToken": "...",
  "projectId": "...",
  "promptText": "Combine these 3 images",
  "aspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE",
  "imageModel": "R2I",
  "referenceImages": [
    {"base64": "/9j/4AAQSkZJRgABAQ...", "mimeType": "image/jpeg"},
    {"base64": "/9j/4AAQSkZJRgABAQ...", "mimeType": "image/jpeg"},
    {"base64": "/9j/4AAQSkZJRgABAQ...", "mimeType": "image/jpeg"}
  ]
}
```

---

## 4. Upscale Image 2K/4K

### Request

```http
POST /api/flow/images/upscale
Content-Type: application/json
```

```json
{
  "accessToken": "...",
  "projectId": "...",
  "mediaId": "16333573-64d3-4189-ad1c-74f198939ddb",
  "targetResolution": "UPSAMPLE_IMAGE_RESOLUTION_2K"
}
```

`targetResolution`:
- `UPSAMPLE_IMAGE_RESOLUTION_2K` — 2K
- `UPSAMPLE_IMAGE_RESOLUTION_4K` — 4K

### Response

```json
{
  "success": true,
  "media": [{
    "mediaId": "16333573-...",
    "encodedImage": "/9j/4AAQSkZJ...",
    "targetResolution": "UPSAMPLE_IMAGE_RESOLUTION_2K"
  }],
  "encodedImage": "/9j/4AAQSkZJ...",
  "remainingCredits": 20395
}
```

Backend tự retry 3 lần khi gặp `UNUSUAL_ACTIVITY`. Khi rate-limit 429 → fail fast.

---

## 5. Full example (cURL)

```bash
curl -X POST http://149.28.138.235:9339/api/flow/images/generate-v2 \
  -H "Content-Type: application/json" \
  -d '{
    "accessToken": "ya29.a0AQvPyI...",
    "projectId": "370bdaea-7fa0-4834-8c97-70b2592cf14f",
    "promptText": "majestic dragon flying over mountain peaks at sunset",
    "aspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE",
    "imageModel": "NARWHAL"
  }'
```

## 6. Full example (Python)

```python
import json, urllib.request, base64

VPS = "http://149.28.138.235:9339"
ACCESS_TOKEN = "ya29.a0AQvPyI..."
PROJECT_ID = "370bdaea-7fa0-4834-8c97-70b2592cf14f"

# T2I
body = {
    "accessToken": ACCESS_TOKEN,
    "projectId": PROJECT_ID,
    "promptText": "majestic dragon flying over mountains",
    "aspectRatio": "IMAGE_ASPECT_RATIO_LANDSCAPE",
    "imageModel": "NARWHAL",
}

req = urllib.request.Request(
    f"{VPS}/api/flow/images/generate-v2",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=120) as resp:
    result = json.loads(resp.read())

if result["success"]:
    media = result["media"][0]
    print(f"mediaId: {media['mediaId']}")
    print(f"URL: {media['fifeUrl']}")
    print(f"Dimensions: {media['dimensions']}")
```

## 7. Full example (JavaScript)

```javascript
const VPS = "http://149.28.138.235:9339";

async function generateImage() {
  const resp = await fetch(`${VPS}/api/flow/images/generate-v2`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({
      accessToken: "ya29.a0AQvPyI...",
      projectId: "370bdaea-7fa0-4834-8c97-70b2592cf14f",
      promptText: "majestic dragon flying over mountains",
      aspectRatio: "IMAGE_ASPECT_RATIO_LANDSCAPE",
      imageModel: "NARWHAL",
    }),
  });
  const data = await resp.json();
  console.log("mediaId:", data.media[0].mediaId);
  console.log("URL:", data.media[0].fifeUrl);
  return data;
}
```

---

## 8. Error handling

| HTTP | Detail | Action |
|------|--------|--------|
| 200 | success: true | OK |
| 401 | `Token xác thực hết hạn hoặc không hợp lệ` | Refresh access token |
| 403 | `Hoạt động bất thường, đang thử lại...` | Captcha score thấp — retry sau 30-60s |
| 403 | `Vi phạm chính sách...` | Content moderation reject — đổi prompt |
| 429 | `Quá nhiều request, vui lòng chờ 20s` | Rate limit — chờ 20s+ |
| 500 | `Lỗi tạo nội dung` | Bad request format, check payload |
| 503 | `reCAPTCHA mint timeout (45s)` | Chrome hang, restart backend |
| 503 | `Lỗi kết nối tới Google API` | Network issue |

Retry strategy:
- **403 UNUSUAL_ACTIVITY**: retry với captcha mới sau 30s
- **429 TOO_MUCH_TRAFFIC**: chờ 60s+
- **503 transient**: retry 3 lần với delay 10s
- **4xx khác**: không retry, sửa request

---

## 9. Timing benchmarks

| Operation | Avg time |
|-----------|----------|
| Mint captcha | 5-15s |
| T2I generate | 25-40s (mint + Flow API) |
| I2I generate (1 ref) | 30-40s |
| I2I generate (3 refs) | 30-45s |
| Upscale 2K | 30-50s |
| Upscale 4K | 60-90s |

---

## 10. Best practices

1. **Auto-pick model**: `R2I` nếu có reference, `NARWHAL` nếu không
2. **Delay 15-20s** giữa các requests cùng project (tránh 429)
3. **Reuse captcha không khả thi** — mỗi Flow API call cần token mới
4. **Download `fifeUrl` ngay** khi nhận — URL expire sau ~48h
5. **Cache `seed`** nếu muốn reproduce ảnh giống
6. **Handle 403 UNUSUAL_ACTIVITY** bằng cooldown, không spam retry
