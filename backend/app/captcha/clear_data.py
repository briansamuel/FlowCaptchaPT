"""
Periodic Chrome browsing data cleaner.
Clears: browsing history, cookies, cache, download history.

Approach:
- CDP: Network.clearBrowserCache, Network.clearBrowserCookies (reliable)
- File system: Delete History/Download SQLite data from Chrome profile dirs
"""
from __future__ import annotations
import asyncio
import logging
import os
import socket
import sqlite3
import shutil
from pathlib import Path
from typing import Callable, List, Optional

from .cdp_client import RawCDPClient

logger = logging.getLogger(__name__)

_clear_task: Optional[asyncio.Task] = None


def _clear_history_files(profile_dir: str):
    """Clear browsing history, download history, and cookies from Chrome SQLite files."""
    profile = Path(profile_dir)

    # Chrome stores data in "Default/" subfolder or directly in profile root
    search_roots = [profile / "Default", profile]

    for root in search_roots:
        if not root.exists():
            continue

        # --- Browsing history + Download history ---
        history_file = root / "History"
        if history_file.exists():
            try:
                conn = sqlite3.connect(str(history_file))
                cursor = conn.cursor()
                cursor.execute("DELETE FROM urls")
                cursor.execute("DELETE FROM visits")
                try:
                    cursor.execute("DELETE FROM visit_source")
                except sqlite3.OperationalError:
                    pass
                try:
                    cursor.execute("DELETE FROM downloads")
                    cursor.execute("DELETE FROM downloads_url_chains")
                except sqlite3.OperationalError:
                    pass
                conn.commit()
                conn.close()
                logger.info(f"[ClearData] ✓ Cleared history DB: {history_file}")
            except Exception as e:
                logger.warning(f"[ClearData] Could not clear {history_file}: {e}")

        # --- Cookies ---
        cookies_file = root / "Cookies"
        if cookies_file.exists():
            try:
                conn = sqlite3.connect(str(cookies_file))
                cursor = conn.cursor()
                cursor.execute("DELETE FROM cookies")
                conn.commit()
                conn.close()
                logger.info(f"[ClearData] ✓ Cleared cookies DB: {cookies_file}")
            except Exception as e:
                logger.warning(f"[ClearData] Could not clear {cookies_file}: {e}")

        # --- Web Data (autofill, etc) ---
        webdata_file = root / "Web Data"
        if webdata_file.exists():
            try:
                conn = sqlite3.connect(str(webdata_file))
                cursor = conn.cursor()
                try:
                    cursor.execute("DELETE FROM autofill")
                except sqlite3.OperationalError:
                    pass
                conn.commit()
                conn.close()
            except Exception:
                pass

        # --- Clean journal files ---
        for fname in ["History-journal", "Cookies-journal", "Web Data-journal"]:
            journal = root / fname
            if journal.exists():
                try:
                    journal.unlink()
                except Exception:
                    pass

        # --- Session/Tab data ---
        for fname in ["Current Session", "Current Tabs", "Last Session", "Last Tabs",
                      "Visited Links", "Network Action Predictor"]:
            f = root / fname
            if f.exists():
                try:
                    f.unlink()
                    logger.info(f"[ClearData] ✓ Deleted: {f.name}")
                except Exception:
                    pass


def _clear_cache_dirs(profile_dir: str):
    """Clear cached images and files by removing Cache directories."""
    profile = Path(profile_dir)

    # All possible cache locations (both Default/ and root)
    cache_dirs = [
        profile / "Default" / "Cache",
        profile / "Default" / "Code Cache",
        profile / "Default" / "GPUCache",
        profile / "Default" / "Service Worker" / "CacheStorage",
        profile / "Default" / "Service Worker" / "ScriptCache",
        profile / "Default" / "IndexedDB",
        profile / "Default" / "File System",
        profile / "Default" / "blob_storage",
        profile / "Cache",
        profile / "Code Cache",
        profile / "GPUCache",
        profile / "ShaderCache",
        profile / "GrShaderCache",
    ]

    cleared = 0
    for cache_dir in cache_dirs:
        if cache_dir.exists() and cache_dir.is_dir():
            try:
                shutil.rmtree(str(cache_dir), ignore_errors=True)
                cache_dir.mkdir(parents=True, exist_ok=True)
                cleared += 1
            except Exception as e:
                logger.warning(f"[ClearData] Could not clear cache {cache_dir}: {e}")

    if cleared > 0:
        logger.info(f"[ClearData] ✓ Cleared {cleared} cache directories")


async def clear_browsing_data_cdp(port: int) -> bool:
    """Clear browsing data by opening chrome://settings/clearBrowserData and clicking 'Delete data'.
    This is the same as manually clearing from Chrome's UI - most thorough method.
    Returns True if successful, False if Chrome not reachable.
    """
    # First check if Chrome is actually listening on this port
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(("127.0.0.1", port))
        s.close()
    except (ConnectionRefusedError, OSError, socket.timeout):
        logger.info(f"[ClearData] Chrome not running on port {port}, skipping CDP clear")
        return False

    cdp = RawCDPClient(port)
    tab_id = None
    reused_tab = False
    try:
        await cdp.connect()
        logger.info(f"[ClearData] ➤ Step 1: Connected to Chrome CDP (port={port})")

        # Try to find an existing about:blank tab to reuse (avoids focus steal)
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{port}/json") as resp:
                    targets = await resp.json()
                    for t in targets:
                        if t.get("type") == "page" and t.get("url", "") in ("about:blank", "chrome://newtab/"):
                            tab_id = t["id"]
                            reused_tab = True
                            break
        except Exception:
            pass

        # If no blank tab found, create one (background - no focus steal)
        if not tab_id:
            tab_id = await cdp.create_tab("about:blank", background=True)
            logger.info(f"[ClearData] ➤ Step 2: Created background tab")
        else:
            logger.info(f"[ClearData] ➤ Step 2: Reusing existing blank tab")

        # Attach and navigate to settings (clearBrowserData URL auto-opens the dialog)
        page = await cdp.attach_to_target(tab_id)
        await page.navigate("chrome://settings/clearBrowserData")
        logger.info(f"[ClearData] ➤ Step 3: Navigated to chrome://settings/clearBrowserData")

        # Wait for page + dialog to load (reduced for VPS)
        await asyncio.sleep(3)

        # Re-attach
        page = await cdp.attach_to_target(tab_id)
        logger.info(f"[ClearData] ➤ Step 4: Settings page loaded, executing clear script...")

        # Script using recursive shadow DOM search - works across Chrome versions
        clear_script = """
        (async () => {
            const sleep = ms => new Promise(r => setTimeout(r, ms));
            
            // Recursive search through all shadow DOMs
            function deepQuery(root, selector) {
                let result = root.querySelector(selector);
                if (result) return result;
                const allElements = root.querySelectorAll('*');
                for (const el of allElements) {
                    if (el.shadowRoot) {
                        result = deepQuery(el.shadowRoot, selector);
                        if (result) return result;
                    }
                }
                return null;
            }
            
            // Wait for dialog to appear (clearBrowserData URL should auto-open it)
            let deleteBtn = null;
            for (let i = 0; i < 10; i++) {
                deleteBtn = deepQuery(document, '#deleteButton');
                if (deleteBtn) break;
                await sleep(1000);
            }
            
            if (!deleteBtn) {
                // Try clicking the clearBrowsingData link if dialog didn't auto-open
                const clearLink = deepQuery(document, '#clearBrowsingData');
                if (clearLink) {
                    clearLink.click();
                    await sleep(2000);
                    deleteBtn = deepQuery(document, '#deleteButton');
                }
            }
            
            if (!deleteBtn) return 'error: #deleteButton not found';
            
            // Try to set time range to "All time"
            const timePicker = deepQuery(document, '#timePicker');
            if (timePicker) {
                const selectEl = timePicker.shadowRoot ? 
                    timePicker.shadowRoot.querySelector('select') : null;
                if (selectEl) {
                    selectEl.value = '4';
                    selectEl.dispatchEvent(new Event('change', {bubbles: true}));
                    await sleep(500);
                }
            }
            
            // Make sure all checkboxes are checked
            const dialog = deepQuery(document, '#deleteBrowsingDataDialog');
            if (dialog) {
                const checkboxes = dialog.querySelectorAll('settings-checkbox');
                checkboxes.forEach(cb => {
                    if (!cb.hasAttribute('checked')) {
                        cb.setAttribute('checked', '');
                        cb.click();
                    }
                });
                await sleep(300);
            }
            
            // Click Delete data
            deleteBtn.click();
            await sleep(3000);
            
            return 'success: clicked Delete data button';
        })()
        """

        result = await page.evaluate(clear_script, timeout=20)
        logger.info(f"[ClearData] ➤ Step 5: Script result: {result}")

        if result and 'success' in str(result):
            logger.info(f"[ClearData] ✓ SUCCESS - Browsing data deleted via Chrome Settings UI (port={port})")
            # Wait for Chrome to finish clearing
            await asyncio.sleep(2)
        else:
            logger.warning(f"[ClearData] ✗ Settings page clear failed: {result}")
            # Fallback: try Network commands
            try:
                await page.send("Network.enable")
                await page.send("Network.clearBrowserCache")
                await page.send("Network.clearBrowserCookies")
                logger.info(f"[ClearData] ✓ Fallback: CDP Network clear done (port={port})")
            except Exception:
                pass

        return True
    except Exception as e:
        logger.warning(f"[ClearData] ✗ CDP clear failed (port={port}): {e}")
        return False
    finally:
        # Close or reset the tab
        if tab_id and cdp:
            try:
                if reused_tab:
                    page = await cdp.attach_to_target(tab_id)
                    await page.send("Page.navigate", {"url": "about:blank"})
                    logger.info(f"[ClearData] ➤ Step 6: Tab reset to about:blank")
                else:
                    await cdp.close_tab(tab_id)
                    logger.info(f"[ClearData] ➤ Step 6: Settings tab closed")
            except Exception:
                pass
        try:
            await cdp.close()
        except Exception:
            pass


async def clear_all_data(profile_dirs: List[str], cdp_ports: List[int]):
    """
    Full clear of browsing data:
    1. CDP: clear cache + cookies for each running Chrome instance (with timeout)
    2. File system: clear history DB + cache directories for each profile
    """
    logger.info("=" * 60)
    logger.info("[ClearData] Starting scheduled clear browsing data...")
    logger.info(f"[ClearData] Profiles: {len(profile_dirs)}, CDP ports: {cdp_ports}")
    logger.info("=" * 60)

    # Step 1: CDP clear (with hard timeout to prevent stuck)
    CDP_CLEAR_TIMEOUT = 40  # seconds max per port
    for port in cdp_ports:
        if port:
            try:
                await asyncio.wait_for(clear_browsing_data_cdp(port), timeout=CDP_CLEAR_TIMEOUT)
            except asyncio.TimeoutError:
                logger.error(f"[ClearData] ✗ CDP clear TIMEOUT after {CDP_CLEAR_TIMEOUT}s (port={port}) - skipping")
            except Exception as e:
                logger.error(f"[ClearData] ✗ CDP clear error (port={port}): {e}")

    # Step 2: File system clear (instant, never blocks)
    for profile_dir in profile_dirs:
        logger.info(f"[ClearData] Clearing files for profile: {profile_dir}")
        _clear_history_files(profile_dir)
        _clear_cache_dirs(profile_dir)

    logger.info("=" * 60)
    logger.info("[ClearData] ✓ Clear browsing data completed!")
    logger.info("=" * 60)


async def _periodic_clear(interval_minutes: int, get_info_fn: Callable):
    """Background task that clears data every N minutes."""
    interval_seconds = interval_minutes * 60
    logger.info(f"[ClearData] ✓ Scheduler ACTIVE - will clear every {interval_minutes} minutes ({interval_seconds}s)")
    cycle = 0

    while True:
        cycle += 1
        logger.info(f"[ClearData] ⏳ Next clear in {interval_minutes} minutes (cycle #{cycle})...")

        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("[ClearData] Scheduler cancelled during sleep")
            return

        logger.info(f"[ClearData] ⏰ Timer fired! Starting clear cycle #{cycle}...")

        try:
            info = get_info_fn()
            profile_dirs = info.get("profile_dirs", [])
            cdp_ports = info.get("cdp_ports", [])

            logger.info(f"[ClearData] Collected info: profiles={profile_dirs}, ports={cdp_ports}")

            if not profile_dirs:
                logger.info("[ClearData] No profiles found, skipping this cycle")
                continue

            await clear_all_data(profile_dirs, cdp_ports)

        except asyncio.CancelledError:
            logger.info("[ClearData] Scheduler cancelled during clear")
            return
        except Exception as e:
            logger.error(f"[ClearData] Scheduler error in cycle #{cycle}: {e}", exc_info=True)
            # Continue running - don't let one error kill the scheduler


def _on_task_done(task: asyncio.Task):
    """Callback when scheduler task finishes (should not happen normally)."""
    try:
        exc = task.exception()
        if exc:
            logger.error(f"[ClearData] Scheduler task died with exception: {exc}", exc_info=exc)
        else:
            logger.warning("[ClearData] Scheduler task ended unexpectedly")
    except asyncio.CancelledError:
        logger.info("[ClearData] Scheduler task was cancelled")


def start_clear_data_scheduler(interval_minutes: int, get_info_fn: Callable):
    """
    Start the periodic clear data background task.

    get_info_fn should return:
    {
        "profile_dirs": ["/path/to/profile1", ...],
        "cdp_ports": [19284, 19285, ...]
    }
    """
    global _clear_task
    if interval_minutes <= 0:
        logger.info("[ClearData] Scheduler disabled (interval=0)")
        return
    if _clear_task and not _clear_task.done():
        logger.info("[ClearData] Scheduler already running")
        return
    _clear_task = asyncio.ensure_future(_periodic_clear(interval_minutes, get_info_fn))
    _clear_task.add_done_callback(_on_task_done)
    logger.info(f"[ClearData] Scheduler registered - will clear every {interval_minutes} min")


def stop_clear_data_scheduler():
    """Stop the periodic clear data background task."""
    global _clear_task
    if _clear_task and not _clear_task.done():
        _clear_task.cancel()
        _clear_task = None
        logger.info("[ClearData] Scheduler stopped")
