# FlowCaptchaPT

reCAPTCHA Enterprise Token Service for Google Flow (labs.google/fx/tools/flow).

Launches real Chrome (non-headless), connects via raw CDP websocket, extracts captcha tokens. No Playwright automation layer = no `Runtime.enable` detection.

## Paths

| Component | Path |
|-----------|------|
| Backend | `E:\DockerContainer\Python\FlowCaptchaPT\backend\` |
| Frontend | `E:\DockerContainer\Python\FlowCaptchaPT\frontend\` |
| Chrome Profile | `E:\DockerContainer\Python\FlowCaptchaPT\backend\chrome-profile\` |
| SQLite DB | `E:\DockerContainer\Python\FlowCaptchaPT\backend\data\flowcaptcha.db` |
| Config | `E:\DockerContainer\Python\FlowCaptchaPT\backend\.env` |

## Run Commands

```bash
# Activate venv
cd E:\DockerContainer\Python\FlowCaptchaPT\backend
.\venv\Scripts\activate

# Start server
python -m uvicorn app.main:app --reload --port 8899

# Or without reload
python -m uvicorn app.main:app --port 8899
```

## First Time Setup

1. Start server
2. Create API key: Dashboard → API Keys → Create Key
3. Import Google cookies OR call login endpoint to open Chrome for manual login
4. Test token extraction

## API Endpoints

### Public (API Key auth: `X-Api-Key` header)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/captcha` | Get captcha token `{action: "VIDEO_GENERATION"\|"IMAGE_GENERATION"}` |
| GET | `/api/captcha/jobs/{job_id}` | Poll queued job status |
| POST | `/api/captcha/callback/{log_id}` | Report token success/failure |
| POST | `/api/captcha/login` | Open Chrome for manual Google login |
| POST | `/api/captcha/import-cookies` | Import cookies into Chrome profile |

### Admin (Bearer token: `Authorization: Bearer <admin_token>`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/POST/DELETE | `/api/keys` | CRUD API keys |
| PUT | `/api/keys/{id}/toggle` | Enable/disable key |
| GET/PUT | `/api/settings` | Runtime settings |
| GET | `/api/dashboard/stats` | Usage statistics |
| GET | `/api/logs` | Usage logs (paginated, filterable) |
| GET | `/api/health` | Health check |

### Frontend Pages

| Page | URL |
|------|-----|
| Dashboard | `http://localhost:8899/` |
| API Keys | `http://localhost:8899/keys.html` |
| Import Cookies | `http://localhost:8899/cookies.html` |
| Logs | `http://localhost:8899/logs.html` |
| Settings | `http://localhost:8899/settings.html` |
| API Docs | `http://localhost:8899/docs.html` |

## Cloudflare Tunnel + Domain

Domain: `captcha.autosdvn.top`
Tunnel ID: `8a5ed565-e838-45d6-a98f-68015d64d8db`
Config file: `cloudflared.yml` (project root)

```bash
# Run tunnel
cloudflared tunnel --config cloudflared.yml run
```

Cloudflare config (`cloudflared.yml`):
```yaml
tunnel: 8a5ed565-e838-45d6-a98f-68015d64d8db
credentials-file: C:\Users\Brian\.cloudflared\8a5ed565-e838-45d6-a98f-68015d64d8db.json

ingress:
  - hostname: captcha.autosdvn.top
    service: http://localhost:8899
  - service: http_status:404
```

Public endpoint:
```
POST https://captcha.autosdvn.top/api/captcha
X-Api-Key: fc_your_key
{"action": "VIDEO_GENERATION"}
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| FC_ADMIN_TOKEN | flowcaptcha-admin-2024 | Admin auth token |
| FC_MAX_CONCURRENT | 3 | Max simultaneous extractions |
| FC_HEADLESS | false | Chrome headless mode (false = non-headless) |
| FC_DEFAULT_COOLDOWN | 10 | Seconds between successful requests |
| FC_DEFAULT_COOLDOWN_FAIL | 120 | Seconds after failed request |
| FC_DEFAULT_WAIT_DELAY | 15 | Seconds to wait before extracting token |

## Architecture

```
Client → API (FastAPI) → CaptchaService → Chrome (non-headless) → CDP → labs.google
                                              ↑
                                    Persistent profile with Google login
                                    Chrome stays alive between requests
```

- Real Chrome binary (not Playwright Chromium)
- Raw CDP websocket (no Playwright = no Runtime.enable)
- Chrome stays alive between requests (faster subsequent extractions)
- Persistent profile preserves Google login session
- Job queue for concurrent request handling

## Docker Deployment (VPS/Linux)

```bash
docker-compose up -d --build
```

Container tự động:
- Cài Google Chrome stable
- Start Xvfb virtual display (:99)
- Chrome chạy non-headless trên display ảo (reCAPTCHA thấy là browser thật)
- `shm_size: 512m` cho Chrome shared memory

Volumes persist data giữa restart:
- `chrome-profile/` — Google login session
- `data/` — SQLite DB

Note: Non-headless mode requires X11/display server. For VPS without display, use `FC_HEADLESS=true` or install Xvfb:
```bash
apt install xvfb
xvfb-run python -m uvicorn app.main:app --port 8899
```
