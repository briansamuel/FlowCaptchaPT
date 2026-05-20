"""FlowCaptchaPT - Captcha Token Service."""
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .version import APP_VERSION

_start_time = time.time()
from .database import init_db
from .models import ProxySetting  # noqa: F401 — register model before create_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing database...")
    await init_db()
    logger.info(f"DB: {settings.database_url}")
    logger.info(f"Chrome profile: {settings.chrome_profile_path}")
    logger.info(f"Headless: {settings.headless}")
    logger.info(f"Max concurrent: {settings.max_concurrent}")

    # Start clear data scheduler
    from .captcha.clear_data import start_clear_data_scheduler, stop_clear_data_scheduler
    from .captcha.profile_manager import get_profile_manager

    def _get_clear_data_info():
        """Collect all profile dirs and CDP ports from the profile manager."""
        pm = get_profile_manager()
        profile_dirs = []
        cdp_ports = []
        for svc in pm._services:
            profile_dirs.append(svc.profile_dir)
            port = svc._cdp_port or getattr(svc, '_cdp_port_override', None)
            if port:
                cdp_ports.append(port)
        return {"profile_dirs": profile_dirs, "cdp_ports": cdp_ports}

    if settings.clear_data_interval > 0:
        start_clear_data_scheduler(settings.clear_data_interval, _get_clear_data_info)
        logger.info(f"Clear data scheduler: every {settings.clear_data_interval} minutes")
    else:
        logger.info(f"Clear data scheduler: DISABLED (FC_CLEAR_DATA_INTERVAL={settings.clear_data_interval})")

    yield

    stop_clear_data_scheduler()
    from .captcha.service import get_captcha_service
    svc = get_captcha_service()
    await svc.close()
    logger.info("Shutdown complete")


app = FastAPI(
    title="FlowCaptchaPT",
    description="reCAPTCHA Enterprise Token Service + Google Labs Flow API Proxy",
    version=APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API routes
from .api.captcha import router as captcha_router
from .api.keys import router as keys_router
from .api.settings_api import router as settings_router
from .api.dashboard import router as dashboard_router
from .api.logs import router as logs_router
from .api.flow import router as flow_router

app.include_router(captcha_router)
app.include_router(keys_router)
app.include_router(settings_router)
app.include_router(dashboard_router)
app.include_router(logs_router)
app.include_router(flow_router)


@app.get("/api/health")
async def health():
    uptime_s = int(time.time() - _start_time)
    hours, remainder = divmod(uptime_s, 3600)
    minutes, secs = divmod(remainder, 60)
    return {
        "status": "ok",
        "version": APP_VERSION,
        "uptime": f"{hours}h{minutes}m{secs}s",
        "uptime_seconds": uptime_s,
    }


# Serve frontend static files at /ui/
# Check both Docker layout (/app/frontend) and local dev layout (../../frontend)
_app_dir = Path(__file__).resolve().parent.parent
frontend_dir = _app_dir / "frontend"
if not frontend_dir.exists():
    frontend_dir = _app_dir.parent / "frontend"
if frontend_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
