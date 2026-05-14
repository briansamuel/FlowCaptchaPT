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

from ..config import settings as _settings

MAX_TAB_POOL = _settings.max_tab_pool

# Only for blocking ensure_chrome() — 2 workers is enough
_executor = ThreadPoolExecutor(max_workers=2)

SITE_KEY = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV"
TARGET_URL = "https://labs.google/fx"
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
        self._cdp_port_override: Optional[int] = None  # For multi-profile port assignment
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
        port = self._cdp_port or self._cdp_port_override or 19284
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect(("127.0.0.1", port))
            s.close()
            self._cdp_port = port
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

            self._kill_stale_chrome()
            _time.sleep(2)
            self._cleanup_locks()
            self._warm_tabs = {}

            port_file = Path(self.profile_dir) / "DevToolsActivePort"
            if port_file.exists():
                try:
                    port_file.unlink()
                except Exception:
                    pass

            cdp_port = self._cdp_port_override or 19284

            args = [
                chrome_path,
                f"--user-data-dir={self.profile_dir}",
                f"--remote-debugging-port={cdp_port}",
                "--no-first-run",
                "--no-default-browser-check",
                "--password-store=basic",
                "--disable-sync",
                "--disable-translate",
                "--disable-default-apps",
                "--disable-crash-reporter",
                "--disable-breakpad",
                "--disable-background-networking",
                "--disable-client-side-phishing-detection",
                "--disable-focus-stealing-fix",
                "--no-focus-on-navigate",
                "--window-size=1280,900",
                "about:blank",
            ]

            if sys.platform != "win32":
                args += ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]

            if self.headless:
                args.append("--headless=new")

            from ..config import proxy_pool
            p = proxy_pool.chrome_proxy_entry()
            if p and not p.user:
                args.append(f"--proxy-server={p.chrome_arg}")
                logger.info(f"Chrome proxy: {p.chrome_arg}")
            elif p and p.user:
                logger.info(f"Chrome proxy skipped (SOCKS5 auth not supported by Chrome CLI)")

            logger.info(f"Launching Chrome: headless={self.headless} port={cdp_port} path={chrome_path}")
            logger.debug(f"Chrome args: {' '.join(args[1:])}")

            log_path = Path(self.profile_dir).parent / "chrome-stderr.log"
            chrome_log = open(log_path, "w", encoding="utf-8", errors="replace")

            popen_kw = {
                "stdout": subprocess.DEVNULL,
                "stderr": chrome_log,
            }
            if sys.platform == "win32":
                popen_kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

            self._chrome_proc = subprocess.Popen(args, **popen_kw)
            self._chrome_log_fp = chrome_log

            import socket
            deadline = _time.time() + 15
            while _time.time() < deadline:
                if self._chrome_proc.poll() is not None:
                    exit_code = self._chrome_proc.returncode
                    try:
                        chrome_log.close()
                        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                            stderr_tail = f.read()[-3000:]
                    except Exception:
                        stderr_tail = ""
                    logger.error(f"Chrome exited with code {exit_code}")
                    if stderr_tail.strip():
                        logger.error(f"Chrome stderr: {stderr_tail}")
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
        """Kill Chrome process owned by this service (by PID if available, else by port)."""
        import subprocess as sp
        # If we have a known process, kill it directly
        if self._chrome_proc and self._chrome_proc.poll() is None:
            try:
                self._chrome_proc.kill()
                self._chrome_proc.wait(timeout=5)
                logger.info(f"Killed own Chrome process (pid={self._chrome_proc.pid})")
            except Exception:
                pass
            self._chrome_proc = None
            _time.sleep(1)
            return

        # Fallback: kill by port (only if no other instances running)
        port = self._cdp_port_override or self._cdp_port or 19284
        try:
            if sys.platform == "win32":
                # Find PID listening on our specific port
                result = sp.run(
                    ["netstat", "-ano"],
                    capture_output=True, text=True, timeout=10,
                )
                for line in result.stdout.splitlines():
                    if f":{port}" in line and "LISTENING" in line:
                        parts = line.split()
                        pid = parts[-1]
                        sp.run(["taskkill", "/PID", pid, "/F"], capture_output=True, timeout=5)
                        logger.info(f"Killed Chrome on port {port} (pid={pid})")
                        _time.sleep(1)
                        break
            else:
                # Linux: kill process using our port
                result = sp.run(
                    ["fuser", f"{port}/tcp"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.stdout.strip():
                    pids = result.stdout.strip().split()
                    for pid in pids:
                        sp.run(["kill", "-9", pid], capture_output=True, timeout=5)
                    logger.info(f"Killed Chrome on port {port} (pids={pids})")
                    _time.sleep(1)
        except Exception:
            # Last resort for first launch: kill all chrome
            try:
                if sys.platform == "win32":
                    sp.run(["taskkill", "/IM", "chrome.exe", "/F"], capture_output=True, timeout=10)
                else:
                    sp.run(["pkill", "-9", "-f", "chrome"], capture_output=True, timeout=5)
                _time.sleep(2)
            except Exception:
                pass

    async def _setup_warm_tab(self, cdp, slot: int = 0):
        """Create a new tab on labs.google/fx and wait for reCAPTCHA to load naturally."""
        old_tab = self._warm_tabs.get(slot)
        if old_tab:
            try:
                await cdp.close_tab(old_tab)
                logger.debug(f"Closed zombie tab in slot {slot}: {old_tab[:12]}")
            except Exception:
                pass

        target_id = await cdp.create_tab(TARGET_URL, background=True)
        page = await cdp.attach_to_target(target_id)
        self._warm_tabs[slot] = target_id

        # Wait for page load
        for _ in range(30):
            try:
                state = await page.evaluate("document.readyState")
                if state in ("complete", "interactive"):
                    break
            except Exception:
                pass
            await asyncio.sleep(1)

        # Let page settle and reCAPTCHA script initialize naturally
        await asyncio.sleep(2)

        # Single evaluate: check if reCAPTCHA loaded, inject if not, then wait
        bootstrap_js = f"""
            (async () => {{
                // Check if already loaded by the page
                if (typeof grecaptcha !== 'undefined' && grecaptcha.enterprise
                    && typeof grecaptcha.enterprise.execute === 'function') {{
                    return 'ready';
                }}
                // Inject script if not present
                const existing = document.querySelector('script[src*="recaptcha/enterprise"]');
                if (!existing) {{
                    const s = document.createElement('script');
                    s.src = '{RECAPTCHA_SCRIPT_URL}';
                    document.head.appendChild(s);
                }}
                // Wait for it to become ready
                for (let i = 0; i < 40; i++) {{
                    if (typeof grecaptcha !== 'undefined' && grecaptcha.enterprise
                        && typeof grecaptcha.enterprise.execute === 'function') {{
                        return 'ready';
                    }}
                    await new Promise(r => setTimeout(r, 500));
                }}
                return 'timeout';
            }})()
        """
        result = await page.evaluate(bootstrap_js, timeout=25)
        if result == 'timeout':
            raise RuntimeError("grecaptcha not ready after 20s")

        logger.debug(f"Tab slot {slot} ready (tab={target_id[:12]})")
        return page

    async def _extract_via_cdp(self, action: str, wait_delay: int = 0, slot: int = 0) -> CaptchaResult:
        """Runs on main event loop. Each slot is independent.
        
        Optimized for reCAPTCHA score:
        - Uses CDP Input domain for trusted events (isTrusted=true)
        - Minimizes Runtime.evaluate calls (only 1 for token mint)
        - Longer observation window for reCAPTCHA to collect signals
        """
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
                    # Single evaluate to check both URL and grecaptcha readiness
                    check = await page.evaluate(
                        "(() => {"
                        "  const url = window.location.href;"
                        "  const ready = typeof grecaptcha !== 'undefined' && grecaptcha.enterprise"
                        "    && typeof grecaptcha.enterprise.execute === 'function';"
                        "  return JSON.stringify({url, ready});"
                        "})()"
                    )
                    check_data = json.loads(check) if isinstance(check, str) else {}
                    if check_data.get("ready") and "labs.google" in str(check_data.get("url", "")):
                        logger.debug(f"Reusing warm tab slot {slot}")
                        tab_reused = True
                    else:
                        page = None
                except Exception:
                    page = None
                    self._warm_tabs[slot] = None

            if page is None:
                page = await self._setup_warm_tab(cdp, slot)

            # Simple wait before mint
            effective_delay = min(wait_delay, 5) if tab_reused else min(wait_delay, 10)
            if effective_delay > 0:
                logger.debug(f"Slot {slot}: waiting {effective_delay}s (reused={tab_reused})")
                await asyncio.sleep(effective_delay)

            # --- Token mint: single evaluate call ---
            token_script = f"""
                (async () => {{
                    if (typeof grecaptcha === 'undefined' || !grecaptcha.enterprise
                        || typeof grecaptcha.enterprise.execute !== 'function') {{
                        return JSON.stringify({{ success: false, error: 'grecaptcha not available' }});
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

            # Retry up to 3 times
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
                    tok_preview = result.token[:30]
                    logger.info(f"Got {action} token (slot {slot}, attempt {attempt+1}, tab_reused={tab_reused}, preview={tok_preview}...)")

                    # Auto close tab after extraction if enabled
                    from .config_helper import should_auto_close_tabs
                    auto_close = should_auto_close_tabs()
                    logger.info(f"[AutoClose] auto_close_tabs={auto_close}, slot={slot}, warm_tab={self._warm_tabs.get(slot)}")
                    if auto_close:
                        close_tab_id = self._warm_tabs.get(slot)
                        if close_tab_id and cdp:
                            try:
                                await cdp.close_tab(close_tab_id)
                                logger.info(f"[AutoClose] ✓ Closed tab slot {slot} (id={close_tab_id[:8]}...)")
                            except Exception as e:
                                logger.warning(f"[AutoClose] ✗ Failed to close tab slot {slot}: {e}")
                        elif not close_tab_id:
                            logger.warning(f"[AutoClose] No tab_id for slot {slot}")
                        self._warm_tabs[slot] = None

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


def get_captcha_service() -> "ProfileManager":
    """Get the ProfileManager (replaces direct CaptchaService access).
    Returns ProfileManager which has the same get_token()/open_for_login() interface.
    """
    from .profile_manager import get_profile_manager
    return get_profile_manager()


def get_raw_captcha_service() -> CaptchaService:
    """Get a raw single CaptchaService (for backward compat / cookie import)."""
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
