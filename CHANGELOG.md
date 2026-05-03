# Changelog

## v1.1.0 (2026-05-03)
- Remove API key authentication (public endpoints)
- Add residential proxy support with Chrome extension auth
- Add proxy config via `FC_PROXY` env var
- Separate Docker/local Chrome profiles
- Cloudflare tunnel config for `captcha.autosdvn.top`
- Fix Chrome flags for better reCAPTCHA score
- Xvfb resolution 1920x1080
- SQLite migration: `api_key_id` nullable
- Version displayed in `/api/health` and startup logs

## v1.0.0 (2026-01-25)
- Initial release
- reCAPTCHA Enterprise token extraction via CDP
- API key authentication
- Job queue with cooldown
- Docker deployment with Xvfb
- Frontend dashboard
