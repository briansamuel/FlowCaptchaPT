"""
Captcha Service - Real Chrome + Raw CDP token extraction.
Optimized: Chrome stays alive, reuses tabs, minimal RAM, no window flash.
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

_executor = ThreadPoolExecutor(max_workers=2)

SITE_KEY = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV"
TARGET_URL = "https://labs.google/fx/tools/flow"


@dataclass
class CaptchaResult:
    token: Optional[str] = None
    error: Optional[str] = None
    action: Optional[str] = None


class CaptchaService:
    """Non-headless Chrome + raw CDP. Chrome stays alive, reuses warm tab."""

    def __init__(self, profile_dir: str, headless: bool = False, proxy: str = ""):
        self.profile_dir = profile_dir
        self.headless = headless
        self.proxy = proxy
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._cooldown_until: float = 0
        self.cooldown: int = 10
        self.cooldown_fail: int = 120
        self.wait_delay: int = 15
        self._chrome_proc: Optional[subprocess.Popen] = None
        self._cdp_port: Optional[int] = None
        # Reusable warm tab — keeps grecaptcha loaded
        self._warm_tab_id: Optional[str] = None
        # Lock to prevent multiple Chrome launches
        self._chrome_lock = __import__('threading').Lock()

    def set_concurrency(self, max_concurrent: int):
        self._semaphore = asyncio.Semaphore(max_concurrent)

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
            if f.exists():
                try:
                    f.unlink()
                    logger.info(f"Removed stale lock: {fname}")
                except OSError:
                    # Windows: file locked by another process, force kill Chrome
                    logger.warning(f"Cannot remove {fname}, killing stale Chrome...")
                    self._kill_stale_chrome()
                    import time as _t
                    _t.sleep(1)
                    try:
                        f.unlink()
                        logger.info(f"Removed {fname} after killing Chrome")
                    except Exception as e:
                        logger.error(f"Still cannot remove {fname}: {e}")

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
            self._warm_tab_id = None

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
                "--disable-features=TranslateUI,MediaRouter,OptimizationHints",
                "--disable-component-update",
                "--disable-background-networking",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-ipc-flooding-protection",
                "--disable-hang-monitor",
                "--no-pings",
                "--metrics-recording-only",
                "--window-size=800,600",
                "--window-position=9999,9999",
                "--start-minimized",
            ]

            if self.headless:
                args.append("--headless=new")

            if self.proxy:
                args.append(f"--proxy-server={self.proxy}")
                logger.info(f"Using proxy: {self.proxy}")

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

            # Wait for CDP port
            import socket
            deadline = _time.time() + 30
            while _time.time() < deadline:
                # Check if Chrome crashed
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
                # Linux: use pkill/pgrep
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

    async def _extract_via_cdp(self, action: str, wait_delay: int = 0) -> CaptchaResult:
        """Reuse warm tab if possible, otherwise create new one. Extract token."""
        from .cdp_client import RawCDPClient

        result = CaptchaResult(action=action)
        cdp = None

        try:
            port = self.ensure_chrome()
            cdp = RawCDPClient(port)
            await cdp.connect()

            # Try to reuse warm tab (already has grecaptcha loaded)
            reused = False
            if self._warm_tab_id:
                try:
                    page = await cdp.attach_to_target(self._warm_tab_id)
                    # Verify tab is still on Flow page with grecaptcha
                    url = await page.evaluate("window.location.href")
                    if url and "labs.google" in str(url):
                        ready = await page.evaluate(
                            "typeof grecaptcha !== 'undefined' && grecaptcha.enterprise "
                            "&& typeof grecaptcha.enterprise.execute === 'function'"
                        )
                        if ready:
                            reused = True
                            logger.info("Reusing warm tab (grecaptcha ready)")
                except Exception:
                    self._warm_tab_id = None

            if not reused:
                # Create new tab with Flow URL
                target_id = await cdp.create_tab(TARGET_URL)
                page = await cdp.attach_to_target(target_id)
                self._warm_tab_id = target_id

                # Wait for page load
                for _ in range(30):
                    try:
                        state = await page.evaluate("document.readyState")
                        if state in ("complete", "interactive"):
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(1)

                await asyncio.sleep(2)  # Let async scripts load

                # Wait for grecaptcha
                for i in range(60):
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
                    result.error = "grecaptcha not ready after 30s"
                    return result

                logger.info("New tab ready, grecaptcha loaded")

            # Wait delay (skip on reused tab if < 3s since it's already warm)
            effective_delay = wait_delay if not reused else min(wait_delay, 3)
            if effective_delay > 0:
                logger.info(f"Waiting {effective_delay}s...")
                await asyncio.sleep(effective_delay)

            # Extract token
            token_script = f"""
                (async () => {{
                    try {{
                        const token = await grecaptcha.enterprise.execute(
                            '{SITE_KEY}', {{ action: '{action}' }}
                        );
                        return JSON.stringify({{ success: true, token: token }});
                    }} catch (e) {{
                        return JSON.stringify({{ success: false, error: e.message }});
                    }}
                }})()
            """
            raw = await page.evaluate(token_script, timeout=30)
            token_result = json.loads(raw) if isinstance(raw, str) else raw

            if token_result.get("success"):
                result.token = token_result["token"]
                logger.info(f"Got {action} token ({'reused' if reused else 'new'} tab)")
            else:
                result.error = token_result.get("error", "Unknown")
                logger.error(f"Token failed: {result.error}")
                # Invalidate warm tab on error
                self._warm_tab_id = None

            return result

        except Exception as e:
            logger.error(f"CDP extraction failed: {e}")
            result.error = str(e)
            self._warm_tab_id = None
            return result
        finally:
            if cdp:
                try:
                    await cdp.close()
                except Exception:
                    pass

    async def _run_in_thread(self, action: str, wait_delay: int = 0) -> CaptchaResult:
        def run():
            if sys.platform == "win32":
                loop = asyncio.ProactorEventLoop()
            else:
                loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(self._extract_via_cdp(action, wait_delay))
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                return result
            except Exception as e:
                return CaptchaResult(action=action, error=str(e))
            finally:
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                except Exception:
                    pass
                loop.close()

        return await asyncio.get_running_loop().run_in_executor(_executor, run)

    async def get_token(self, action: str) -> CaptchaResult:
        now = _time.time()
        if now < self._cooldown_until:
            wait = self._cooldown_until - now
            return CaptchaResult(action=action, error=f"Cooldown active, retry in {int(wait)}s")

        sem = self._semaphore or asyncio.Semaphore(1)
        async with sem:
            result = await self._run_in_thread(action, self.wait_delay)
            if result.token:
                self._cooldown_until = _time.time() + self.cooldown
            else:
                self._cooldown_until = _time.time() + self.cooldown_fail
            return result

    async def open_for_login(self) -> str:
        old_headless = self.headless
        self.headless = False
        try:
            port = self.ensure_chrome()
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
            self._warm_tab_id = None
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
            proxy=settings.proxy,
        )
        _instance.set_concurrency(settings.max_concurrent)
        _instance.cooldown = settings.default_cooldown
        _instance.cooldown_fail = settings.default_cooldown_fail
        _instance.wait_delay = settings.default_wait_delay
    return _instance
