"""
Profile Manager - Anti-fingerprinting via profile diversification.

Strategies:
- ROTATION: 2-3 Chrome profiles, round-robin per request. Google sees multiple "users".
- EPHEMERAL: Fresh temp profile per request, no cookies/cache persistence. Untrackable.
- SINGLE: Legacy single-profile mode (backward compat).
"""
from __future__ import annotations
import asyncio
import itertools
import logging
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from .service import CaptchaService, CaptchaResult

logger = logging.getLogger(__name__)

# CDP port range for multiple Chrome instances
BASE_CDP_PORT = 19284


class ProfileManager:
    """Orchestrates multiple CaptchaService instances for fingerprint diversification."""

    def __init__(
        self,
        strategy: str,
        base_profile_dir: str,
        headless: bool = False,
        rotation_count: int = 3,
        max_concurrent: int = 32,
        max_tab_pool: int = 8,
        cooldown: int = 0,
        cooldown_fail: int = 120,
        wait_delay: int = 10,
    ):
        self.strategy = strategy  # "single", "rotation", "ephemeral"
        self.base_profile_dir = Path(base_profile_dir)
        self._headless = headless
        self.rotation_count = rotation_count
        self.max_concurrent = max_concurrent
        self.max_tab_pool = max_tab_pool
        self._cooldown = cooldown
        self._cooldown_fail = cooldown_fail
        self._wait_delay = wait_delay

        # Rotation state
        self._services: list[CaptchaService] = []
        self._rotation_cycle = None
        self._rotation_lock = threading.Lock()

        # Ephemeral state
        self._ephemeral_semaphore: Optional[asyncio.Semaphore] = None

        self._init_strategy()

    def _init_strategy(self):
        if self.strategy == "single":
            self._init_single()
        elif self.strategy == "rotation":
            self._init_rotation()
        elif self.strategy == "ephemeral":
            self._init_ephemeral()
        else:
            logger.warning(f"Unknown strategy '{self.strategy}', falling back to single")
            self.strategy = "single"
            self._init_single()

    def _init_single(self):
        """Single profile - original behavior."""
        svc = self._create_service(str(self.base_profile_dir), BASE_CDP_PORT)
        self._services = [svc]
        self._rotation_cycle = itertools.cycle(self._services)
        logger.info("Profile strategy: SINGLE")

    def _init_rotation(self):
        """Multiple profiles - round-robin rotation."""
        self._services = []
        for i in range(self.rotation_count):
            profile_dir = self.base_profile_dir.parent / f"chrome-profile-{i}"
            profile_dir.mkdir(parents=True, exist_ok=True)
            port = BASE_CDP_PORT + i
            svc = self._create_service(str(profile_dir), port)
            self._services.append(svc)

        self._rotation_cycle = itertools.cycle(self._services)
        logger.info(
            f"Profile strategy: ROTATION ({self.rotation_count} profiles, "
            f"ports {BASE_CDP_PORT}-{BASE_CDP_PORT + self.rotation_count - 1})"
        )

    def _init_ephemeral(self):
        """Ephemeral mode - single Chrome instance, fresh browser context per request.
        Each request gets an isolated context (like incognito) - no cookies/cache shared.
        """
        max_ephemeral = min(self.max_concurrent, 4)
        self._ephemeral_semaphore = asyncio.Semaphore(max_ephemeral)
        # Single shared Chrome instance for ephemeral mode
        profile_dir = self.base_profile_dir.parent / "chrome-profile-ephemeral"
        profile_dir.mkdir(parents=True, exist_ok=True)
        svc = self._create_service(str(profile_dir), BASE_CDP_PORT)
        self._services = [svc]
        logger.info(f"Profile strategy: EPHEMERAL (max {max_ephemeral} concurrent, single Chrome)")

    def _create_service(self, profile_dir: str, cdp_port: int) -> CaptchaService:
        """Create a CaptchaService with a specific profile and port."""
        svc = CaptchaService(profile_dir=profile_dir, headless=self._headless)
        svc._cdp_port_override = cdp_port
        svc.set_concurrency(self.max_concurrent)
        svc.cooldown = self._cooldown
        svc.cooldown_fail = self._cooldown_fail
        svc.wait_delay = self._wait_delay
        return svc

    def _next_rotation_service(self) -> CaptchaService:
        """Get next service in rotation (thread-safe)."""
        with self._rotation_lock:
            return next(self._rotation_cycle)

    async def get_token(self, action: str) -> CaptchaResult:
        """Get token using the configured strategy."""
        if self.strategy == "ephemeral":
            return await self._get_token_ephemeral(action)
        else:
            # Both "single" and "rotation" use round-robin (single has 1 service)
            return await self._get_token_rotation(action)

    async def _get_token_rotation(self, action: str) -> CaptchaResult:
        """Round-robin across pre-created profiles."""
        svc = self._next_rotation_service()
        logger.debug(f"Rotation: using profile {svc.profile_dir}")
        return await svc.get_token(action)

    async def _get_token_ephemeral(self, action: str) -> CaptchaResult:
        """Use shared Chrome but create isolated browser context per request.
        Each context is like a fresh incognito window - no cookies/cache persist.
        """
        from .cdp_client import RawCDPClient

        async with self._ephemeral_semaphore:
            svc = self._services[0]
            cdp = None
            browser_context_id = None

            try:
                # Ensure Chrome is running
                import asyncio
                loop = asyncio.get_running_loop()
                from .service import _executor
                port = await loop.run_in_executor(_executor, svc.ensure_chrome)

                cdp = RawCDPClient(port)
                await cdp.connect()

                # Create isolated browser context (like incognito - no shared cookies)
                ctx_result = await cdp.send("Target.createBrowserContext", {
                    "disposeOnDetach": True,
                })
                browser_context_id = ctx_result.get("result", {}).get("browserContextId")

                if not browser_context_id:
                    return CaptchaResult(action=action, error="Failed to create browser context")

                # Create tab in the isolated context
                from .service import TARGET_URL, SITE_KEY, RECAPTCHA_SCRIPT_URL
                tab_result = await cdp.send("Target.createTarget", {
                    "url": TARGET_URL,
                    "browserContextId": browser_context_id,
                })
                target_id = tab_result.get("result", {}).get("targetId")
                if not target_id:
                    return CaptchaResult(action=action, error="Failed to create ephemeral tab")

                page = await cdp.attach_to_target(target_id)

                # Wait for page load
                for _ in range(30):
                    try:
                        state = await page.evaluate("document.readyState")
                        if state in ("complete", "interactive"):
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(1)

                await asyncio.sleep(2)

                # Bootstrap reCAPTCHA (single evaluate)
                bootstrap_js = f"""
                    (async () => {{
                        if (typeof grecaptcha !== 'undefined' && grecaptcha.enterprise
                            && typeof grecaptcha.enterprise.execute === 'function') {{
                            return 'ready';
                        }}
                        const existing = document.querySelector('script[src*="recaptcha/enterprise"]');
                        if (!existing) {{
                            const s = document.createElement('script');
                            s.src = '{RECAPTCHA_SCRIPT_URL}';
                            document.head.appendChild(s);
                        }}
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
                ready = await page.evaluate(bootstrap_js, timeout=25)
                if ready == 'timeout':
                    return CaptchaResult(action=action, error="grecaptcha not ready in ephemeral context")

                # Human simulation with trusted CDP input
                await svc._simulate_human_input(page)

                # Observation window
                import random
                await asyncio.sleep(random.uniform(5, 10))
                await svc._simulate_human_input(page)
                await asyncio.sleep(random.uniform(2, 4))

                # Mint token (single evaluate)
                token_script = f"""
                    (async () => {{
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
                import json
                raw = await page.evaluate(token_script, timeout=45)
                token_result = json.loads(raw) if isinstance(raw, str) else raw

                if token_result.get("success") and token_result.get("token"):
                    logger.info(f"Ephemeral: got {action} token")
                    return CaptchaResult(token=token_result["token"], action=action)

                return CaptchaResult(action=action, error=token_result.get("error", "unknown"))

            except Exception as e:
                logger.error(f"Ephemeral request failed: {e}")
                return CaptchaResult(action=action, error=str(e))

            finally:
                # Cleanup: dispose the browser context (closes all its tabs)
                if cdp and browser_context_id:
                    try:
                        await cdp.send("Target.disposeBrowserContext", {
                            "browserContextId": browser_context_id,
                        })
                    except Exception:
                        pass
                if cdp:
                    try:
                        await cdp.close()
                    except Exception:
                        pass

    async def open_for_login(self) -> str:
        """Open Chrome for manual login (uses first rotation profile or base profile)."""
        if self._services:
            return await self._services[0].open_for_login()
        # Fallback for ephemeral mode
        svc = CaptchaService(profile_dir=str(self.base_profile_dir), headless=False)
        return await svc.open_for_login()

    def kill_chrome(self):
        """Kill all Chrome instances."""
        for svc in self._services:
            svc.kill_chrome()

    async def close(self):
        """Shutdown all services."""
        for svc in self._services:
            await svc.close()
        self._services = []

    # --- Compatibility properties for settings_api ---

    @property
    def headless(self):
        return self._headless

    @headless.setter
    def headless(self, value: bool):
        self._headless = value
        for svc in self._services:
            svc.headless = value

    @property
    def cooldown(self):
        return self._cooldown

    @cooldown.setter
    def cooldown(self, value: int):
        self._cooldown = value
        for svc in self._services:
            svc.cooldown = value

    @property
    def cooldown_fail(self):
        return self._cooldown_fail

    @cooldown_fail.setter
    def cooldown_fail(self, value: int):
        self._cooldown_fail = value
        for svc in self._services:
            svc.cooldown_fail = value

    @property
    def wait_delay(self):
        return self._wait_delay

    @wait_delay.setter
    def wait_delay(self, value: int):
        self._wait_delay = value
        for svc in self._services:
            svc.wait_delay = value

    def set_concurrency(self, max_concurrent: int):
        """Update concurrency for all managed services."""
        self.max_concurrent = max_concurrent
        for svc in self._services:
            svc.set_concurrency(max_concurrent)

    @property
    def profile_info(self) -> dict:
        """Return info about current profile strategy for dashboard."""
        info = {
            "strategy": self.strategy,
            "active_profiles": len(self._services),
        }
        if self.strategy == "rotation":
            info["profiles"] = [
                {"dir": svc.profile_dir, "port": getattr(svc, '_cdp_port_override', None)}
                for svc in self._services
            ]
        elif self.strategy == "ephemeral":
            info["max_concurrent"] = self._ephemeral_semaphore._value if self._ephemeral_semaphore else 0
        return info


_manager_instance: Optional[ProfileManager] = None


def get_profile_manager() -> ProfileManager:
    """Get or create the global ProfileManager instance."""
    global _manager_instance
    if _manager_instance is None:
        from ..config import settings
        _manager_instance = ProfileManager(
            strategy=settings.profile_strategy,
            base_profile_dir=str(settings.chrome_profile_path),
            headless=settings.headless,
            rotation_count=settings.rotation_profile_count,
            max_concurrent=settings.max_concurrent,
            max_tab_pool=settings.max_tab_pool,
            cooldown=settings.default_cooldown,
            cooldown_fail=settings.default_cooldown_fail,
            wait_delay=settings.default_wait_delay,
        )
    return _manager_instance
