"""
Captcha Service - Real Chrome + Raw CDP token extraction.
Tab pool: multiple warm tabs for parallel token minting.
Pure async — no per-request threads. Only ensure_chrome() runs in executor.
"""
from __future__ import annotations
import sys
import os
import asyncio
import json
import logging
import subprocess
import time as _time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

MAX_TAB_POOL = 8

# Only for blocking ensure_chrome() — 2 workers is enough
_executor = ThreadPoolExecutor(max_workers=2)

SITE_KEY = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV"
TARGET_URL = "https://labs.google/"
RECAPTCHA_SCRIPT_URL = f"https://www.google.com/recaptcha/enterprise.js?render={SITE_KEY}"


@dataclass
class CaptchaResult:
    token: Optional[str] = None
    error: Optional[str] = None
    action: Optional[str] = None


class CaptchaService:
    """Non-headless Chrome + raw CDP. Tab pool for parallel minting."""

    def __init__(self, profile_dir: str, headless: bool = False):
        self.profile_dir = profile_dir
        self.headless = headless
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._cooldown_until: float = 0
        self.cooldown: int = 10
        self.cooldown_fail: int = 120
        self.wait_delay: int = 15
        self._chrome_proc: Optional[subprocess.Popen] = None
        self._cdp_port: Optional[int] = None
        # Tab pool: slot_index -> tab_id
        self._warm_tabs: dict = {}
        self._tab_queue: Optional[asyncio.Queue] = None
        self._pool_size: int = MAX_TAB_POOL
        # Lock to prevent multiple Chrome launches
        self._chrome_lock = __import__('threading').Lock()

    def set_concurrency(self, max_concurrent: int):
        self._pool_size = min(max_concurrent, MAX_TAB_POOL)
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._warm_tabs = {}
        self._tab_queue = asyncio.Queue()
        for i in range(self._pool_size):
            self._tab_queue.put_nowait(i)
        logger.info(f"Tab pool: {self._pool_size} slots, max_concurrent={max_concurrent}")

    @staticmethod
    def _find_system_chrome() -> Optional[str]:
        candidates = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
        for path in candidates:
            if os.path.isfile(path):
                return path
        return None

    def _cleanup_locks(self):
        profile = Path(self.profile_dir)
        for fname in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
            f = profile / fname
            try:
                f.unlink(missing_ok=True)
            except OSError:
                try:
                    os.remove(str(f))
                except OSError:
                    pass

    def _is_chrome_alive(self) -> bool:
        if not self._chrome_proc or self._chrome_proc.poll() is not None:
            return False
        if not self._cdp_port:
            return False
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect(("127.0.0.1", self._cdp_port))
            s.close()
            return True
        except Exception:
            return False

    def ensure_chrome(self) -> int:
        """Launch Chrome if not running. Returns CDP port. Thread-safe."""
        with self._chrome_lock:
            if self._is_chrome_alive():
                return self._cdp_port

            chrome_path = self._find_system_chrome()
            if not chrome_path:
                raise RuntimeError("Chrome not found. Install Google Chrome.")

            self._cleanup_locks()
            self._kill_stale_chrome()
            self._warm_tabs = {}

            port_file = Path(self.profile_dir) / "DevToolsActivePort"
            if port_file.exists():
                try:
                    port_file.unlink()
                except Exception:
                    pass

            cdp_port = 19222

            args = [
                chrome_path,
                f"--user-data-dir={self.profile_dir}",
                f"--remote-debugging-port={cdp_port}",
                "--no-first-run",
                "--no-default-browser-check",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--password-store=basic",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-plugins",
                "--disable-images",
                "--blink-settings=imagesEnabled=false",
                "--disable-default-apps",
                "--disable-sync",
                "--disable-translate",
                "--disable-features=TranslateUI,MediaRouter,OptimizationHints,VizDisplayCompositor",
                "--disable-component-update",
                "--disable-background-networking",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-ipc-flooding-protection",
                "--disable-hang-monitor",
                "--no-pings",
                "--metrics-recording-only",
                "--disable-crash-reporter",
                "--disable-breakpad",
                "--window-size=800,600",
                "--window-position=9999,9999",
                "--start-minimized",
            ]

            if self.headless:
                args.append("--headless=new")

            args.append(TARGET_URL)

            logger.info(f"Launching Chrome: headless={self.headless} port={cdp_port}")

            popen_kwargs = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
            }
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags = subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 7  # SW_SHOWMINNOACTIVE
                popen_kwargs["startupinfo"] = startupinfo

            self._chrome_proc = subprocess.Popen(args, **popen_kwargs)

            import socket
            deadline = _time.time() + 15
            while _time.time() < deadline:
                if self._chrome_proc.poll() is not None:
                    exit_code = self._chrome_proc.returncode
                    stderr_out = ""
                    try:
                        stderr_out = self._chrome_proc.stderr.read().decode(errors="replace")[:2000]
                    except Exception:
                        pass
                    logger.error(f"Chrome exited with code {exit_code}")
                    if stderr_out:
                        logger.error(f"Chrome stderr: {stderr_out}")
                    self._chrome_proc = None
                    raise RuntimeError(f"Chrome crashed (exit code {exit_code})")
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(1)
                    s.connect(("127.0.0.1", cdp_port))
                    s.close()
                    self._cdp_port = cdp_port
                    logger.info(f"Chrome started on port {cdp_port} (pid={self._chrome_proc.pid})")
                    return cdp_port
                except Exception:
                    pass
                _time.sleep(0.5)

            self._chrome_proc.kill()
            self._chrome_proc = None
            raise RuntimeError(f"Chrome failed to start (port {cdp_port} timeout)")

    def _kill_stale_chrome(self):
        import subprocess as sp
        profile_name = Path(self.profile_dir).name
        try:
            if sys.platform == "win32":
                result = sp.run(
                    ["wmic", "process", "where",
                     f"commandline like '%{profile_name}%' and name='chrome.exe'",
                     "get", "processid"],
                    capture_output=True, text=True, timeout=5
                )
                for line in result.stdout.strip().split("\n"):
                    line = line.strip()
                    if line.isdigit():
                        try:
                            sp.run(["taskkill", "/PID", line, "/F"], capture_output=True, timeout=5)
                            logger.info(f"Killed stale Chrome PID {line}")
                        except Exception:
                            pass
            else:
                result = sp.run(
                    ["pgrep", "-f", f"--user-data-dir=.*{profile_name}"],
                    capture_output=True, text=True, timeout=5
                )
                for line in result.stdout.strip().split("\n"):
                    line = line.strip()
                    if line.isdigit():
                        try:
                            sp.run(["kill", "-9", line], capture_output=True, timeout=5)
                            logger.info(f"Killed stale Chrome PID {line}")
                        except Exception:
                            pass
        except Exception:
            pass

    async def _setup_warm_tab(self, cdp, slot: int = 0):
        """Create a new tab on labs.google and inject reCAPTCHA script."""
        old_tab = self._warm_tabs.get(slot)
        if old_tab:
            try:
                await cdp.close_tab(old_tab)
                logger.debug(f"Closed zombie tab in slot {slot}: {old_tab[:12]}")
            except Exception:
                pass

        target_id = await cdp.create_tab(TARGET_URL)
        page = await cdp.attach_to_target(target_id)
        self._warm_tabs[slot] = target_id

        for _ in range(30):
            try:
                state = await page.evaluate("document.readyState")
                if state in ("complete", "interactive"):
                    break
            except Exception:
                pass
            await asyncio.sleep(1)

        await asyncio.sleep(1)

        inject_js = f"""
            (async () => {{
                if (typeof grecaptcha !== 'undefined' && grecaptcha.enterprise) return 'already';
                return new Promise((resolve, reject) => {{
                    const s = document.createElement('script');
                    s.src = '{RECAPTCHA_SCRIPT_URL}';
                    s.onload = () => resolve('loaded');
                    s.onerror = (e) => reject('script_error');
                    document.head.appendChild(s);
                }});
            }})()
        """
        await page.evaluate(inject_js, timeout=15)

        for i in range(30):
            try:
                ready = await page.evaluate(
                    "typeof grecaptcha !== 'undefined' && grecaptcha.enterprise "
                    "&& typeof grecaptcha.enterprise.execute === 'function'"
                )
                if ready:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)
        else:
            raise RuntimeError("grecaptcha not ready after 15s")

        logger.info(f"Tab slot {slot} ready, grecaptcha injected (tab={target_id[:12]})")
        return page

    async def _extract_via_cdp(self, action: str, wait_delay: int = 0, slot: int = 0) -> CaptchaResult:
        """Runs on main event loop. Each slot is independent."""
        from .cdp_client import RawCDPClient

        result = CaptchaResult(action=action)
        cdp = None

        try:
            loop = asyncio.get_running_loop()
            port = await loop.run_in_executor(_executor, self.ensure_chrome)

            cdp = RawCDPClient(port)
            await cdp.connect()

            # Try to reuse warm tab
            page = None
            tab_reused = False
            tab_id = self._warm_tabs.get(slot)
            if tab_id:
                try:
                    page = await cdp.attach_to_target(tab_id)
                    url = await page.evaluate("window.location.href")
                    if url and "labs.google" in str(url):
                        ready = await page.evaluate(
                            "typeof grecaptcha !== 'undefined' && grecaptcha.enterprise "
                            "&& typeof grecaptcha.enterprise.execute === 'function'"
                        )
                        if ready:
                            logger.info(f"Reusing warm tab slot {slot}")
                            tab_reused = True
                        else:
                            page = None
                    else:
                        page = None
                except Exception:
                    page = None
                    self._warm_tabs[slot] = None

            if page is None:
                page = await self._setup_warm_tab(cdp, slot)

            effective_delay = min(wait_delay, 3) if tab_reused else wait_delay
            if effective_delay > 0:
                logger.info(f"Slot {slot}: waiting {effective_delay}s...")
                await asyncio.sleep(effective_delay)

            # Token script with built-in grecaptcha wait (handles page reload race)
            token_script = f"""
                (async () => {{
                    for (let i = 0; i < 20; i++) {{
                        if (typeof grecaptcha !== 'undefined' && grecaptcha.enterprise
                            && typeof grecaptcha.enterprise.execute === 'function') break;
                        await new Promise(r => setTimeout(r, 500));
                    }}
                    if (typeof grecaptcha === 'undefined' || !grecaptcha.enterprise) {{
                        return JSON.stringify({{ success: false, error: 'grecaptcha not available after wait' }});
                    }}
                    try {{
                        const token = await grecaptcha.enterprise.execute(
                            '{SITE_KEY}', {{ action: '{action}' }}
                        );
                        if (!token) return JSON.stringify({{ success: false, error: 'empty token' }});
                        return JSON.stringify({{ success: true, token: token }});
                    }} catch (e) {{
                        return JSON.stringify({{ success: false, error: e.message }});
                    }}
                }})()
            """

            # Retry up to 3 times: on evaluate exception OR on success:false
            max_attempts = 3
            for attempt in range(max_attempts):
                try:
                    raw = await page.evaluate(token_script, timeout=45)
                except Exception as eval_err:
                    if attempt < max_attempts - 1:
                        logger.warning(f"Slot {slot} attempt {attempt+1}: evaluate exception: {eval_err}")
                        self._warm_tabs[slot] = None
                        page = await self._setup_warm_tab(cdp, slot)
                        continue
                    result.error = str(eval_err)
                    self._warm_tabs[slot] = None
                    return result

                token_result = json.loads(raw) if isinstance(raw, str) else raw

                if token_result.get("success") and token_result.get("token"):
                    result.token = token_result["token"]
                    logger.info(f"Got {action} token (slot {slot}, attempt {attempt+1})")
                    return result

                error = token_result.get("error", "empty token")
                if attempt < max_attempts - 1:
                    logger.warning(f"Slot {slot} attempt {attempt+1}: {error} — recreating tab")
                    self._warm_tabs[slot] = None
                    page = await self._setup_warm_tab(cdp, slot)
                    continue

                result.error = error
                logger.error(f"Token failed slot {slot} after {max_attempts} attempts: {error}")
                self._warm_tabs[slot] = None

            return result

        except Exception as e:
            logger.error(f"CDP extraction failed slot {slot}: {e}")
            result.error = str(e)
            self._warm_tabs[slot] = None
            return result
        finally:
            if cdp:
                try:
                    await cdp.close()
                except Exception:
                    pass

    async def get_token(self, action: str) -> CaptchaResult:
        now = _time.time()
        if now < self._cooldown_until:
            wait = self._cooldown_until - now
            return CaptchaResult(action=action, error=f"Cooldown active, retry in {int(wait)}s")

        queue = self._tab_queue
        if not queue:
            queue = asyncio.Queue()
            queue.put_nowait(0)
            self._tab_queue = queue

        try:
            slot = await asyncio.wait_for(queue.get(), timeout=90)
        except asyncio.TimeoutError:
            return CaptchaResult(action=action, error="Timeout: all tab slots busy")

        try:
            now = _time.time()
            if now < self._cooldown_until:
                wait = self._cooldown_until - now
                return CaptchaResult(action=action, error=f"Cooldown active, retry in {int(wait)}s")

            result = await self._extract_via_cdp(action, self.wait_delay, slot)
            if result.token:
                self._cooldown_until = _time.time() + self.cooldown
            else:
                self._cooldown_until = _time.time() + self.cooldown_fail
            return result
        finally:
            queue.put_nowait(slot)

    async def open_for_login(self) -> str:
        old_headless = self.headless
        self.headless = False
        try:
            loop = asyncio.get_running_loop()
            port = await loop.run_in_executor(_executor, self.ensure_chrome)
            return f"Chrome running on CDP port {port}. Login to Google."
        finally:
            self.headless = old_headless

    def kill_chrome(self):
        if self._chrome_proc:
            try:
                self._chrome_proc.terminate()
                self._chrome_proc.wait(timeout=5)
            except Exception:
                try:
                    self._chrome_proc.kill()
                except Exception:
                    pass
            self._chrome_proc = None
            self._cdp_port = None
            self._warm_tabs = {}
            logger.info("Chrome killed")

    async def close(self):
        self.kill_chrome()
        _executor.shutdown(wait=False)


_instance: Optional[CaptchaService] = None


def get_captcha_service() -> CaptchaService:
    global _instance
    if _instance is None:
        from ..config import settings
        _instance = CaptchaService(
            profile_dir=str(settings.chrome_profile_path),
            headless=settings.headless,
        )
        _instance.set_concurrency(settings.max_concurrent)
        _instance.cooldown = settings.default_cooldown
        _instance.cooldown_fail = settings.default_cooldown_fail
        _instance.wait_delay = settings.default_wait_delay
    return _instance
