"""FlowCaptchaPT - Captcha Token Service."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import init_db

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
    logger.info(f"Proxy: {settings.proxy or 'none'}")
    yield
    from .captcha.service import get_captcha_service
    svc = get_captcha_service()
    await svc.close()
    logger.info("Shutdown complete")


app = FastAPI(
    title="FlowCaptchaPT",
    description="reCAPTCHA Enterprise Token Service",
    version="1.0.0",
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

app.include_router(captcha_router)
app.include_router(keys_router)
app.include_router(settings_router)
app.include_router(dashboard_router)
app.include_router(logs_router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}


# Serve frontend static files at /ui/
# Check both Docker layout (/app/frontend) and local dev layout (../../frontend)
_app_dir = Path(__file__).resolve().parent.parent
frontend_dir = _app_dir / "frontend"
if not frontend_dir.exists():
    frontend_dir = _app_dir.parent / "frontend"
if frontend_dir.exists():
    app.mount("/ui", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
