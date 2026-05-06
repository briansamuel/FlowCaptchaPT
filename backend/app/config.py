"""Application configuration."""
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Admin
    admin_token: str = "flowcaptcha-admin-2024"

    # Paths
    base_dir: Path = Path(__file__).resolve().parent.parent
    db_path: str = "data/flowcaptcha.db"
    chrome_profile_dir: str = "chrome-profile"

    # Captcha defaults
    max_concurrent: int = 32
    max_tab_pool: int = 8
    default_cooldown: int = 0
    default_cooldown_fail: int = 120
    default_wait_delay: int = 3
    headless: bool = False

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    class Config:
        env_file = ".env"
        env_prefix = "FC_"

    @property
    def database_url(self) -> str:
        db = self.base_dir / self.db_path
        db.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{db}"

    @property
    def chrome_profile_path(self) -> Path:
        p = self.base_dir / self.chrome_profile_dir
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()
