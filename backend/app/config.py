"""Application configuration."""
import itertools
import threading
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings


class ProxyEntry:
    def __init__(self, host: str, port: int, user: str = "", password: str = "", proxy_type: str = "socks5"):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.proxy_type = proxy_type

    @property
    def url(self) -> str:
        auth = f"{self.user}:{self.password}@" if self.user else ""
        return f"{self.proxy_type}://{auth}{self.host}:{self.port}"

    @property
    def chrome_arg(self) -> str:
        return f"socks5://{self.host}:{self.port}"

    def to_dict(self) -> dict:
        return {
            "host": self.host, "port": self.port,
            "user": self.user, "password": self.password,
            "type": self.proxy_type,
        }

    @staticmethod
    def from_string(s: str) -> "ProxyEntry":
        """Parse host:port:user:pass format."""
        parts = s.strip().split(":")
        return ProxyEntry(
            host=parts[0] if len(parts) > 0 else "",
            port=int(parts[1]) if len(parts) > 1 else 0,
            user=parts[2] if len(parts) > 2 else "",
            password=parts[3] if len(parts) > 3 else "",
        )


class ProxyPool:
    def __init__(self):
        self.enabled: bool = False
        self.proxies: List[ProxyEntry] = []
        self._cycle = itertools.cycle([])
        self._lock = threading.Lock()

    def set_proxies(self, proxies: List[ProxyEntry]):
        with self._lock:
            self.proxies = proxies
            self._cycle = itertools.cycle(proxies) if proxies else itertools.cycle([])

    def next(self) -> ProxyEntry | None:
        if not self.enabled or not self.proxies:
            return None
        with self._lock:
            return next(self._cycle, None)

    def chrome_proxy(self) -> str:
        """Return first proxy for Chrome (Chrome uses one fixed proxy)."""
        if not self.enabled or not self.proxies:
            return ""
        return self.proxies[0].chrome_arg

    def chrome_proxy_entry(self) -> ProxyEntry | None:
        if not self.enabled or not self.proxies:
            return None
        return self.proxies[0]


proxy_pool = ProxyPool()


class Settings(BaseSettings):
    # Admin
    admin_token: str = "flowcaptcha-admin-2024"

    # Paths
    base_dir: Path = Path(__file__).resolve().parent.parent
    db_path: str = "data/flowcaptcha.db"
    chrome_profile_dir: str = "chrome-profile"

    # Profile strategy: "single" | "rotation" | "ephemeral"
    profile_strategy: str = "rotation"
    rotation_profile_count: int = 3

    # Captcha defaults
    max_concurrent: int = 32
    max_tab_pool: int = 8
    default_cooldown: int = 0
    default_cooldown_fail: int = 120
    default_wait_delay: int = 10
    headless: bool = False

    # Clear browsing data interval (minutes, 0 = disabled)
    clear_data_interval: int = 5

    # Auto close tabs after token extraction (true = close tab after each extraction)
    auto_close_tabs: bool = True

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
