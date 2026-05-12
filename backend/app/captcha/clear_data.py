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

        # If no blank tab found, create one (background)
        if not tab_id:
            tab_id = await cdp.create_tab("about:blank")

        # Attach and navigate to settings
        page = await cdp.attach_to_target(tab_id)
        await page.navigate("chrome://settings/privacy")
        logger.info(f"[ClearData] Navigated to chrome://settings/privacy (port={port})")

        # Wait for page to load
        await asyncio.sleep(3)

        # Attach to the tab
        page = await cdp.attach_to_target(tab_id)

        # Wait for settings page to fully render
        await asyncio.sleep(2)

        # Script to click "Delete browsing data" link, select "All time", then click "Delete data"
        clear_script = """
        (async () => {
            const sleep = ms => new Promise(r => setTimeout(r, ms));
            
            // Navigate through shadow DOM to find the privacy page
            const settingsUi = document.querySelector('settings-ui');
            if (!settingsUi || !settingsUi.shadowRoot) return 'error: no settings-ui';
            
            const settingsMain = settingsUi.shadowRoot.querySelector('settings-main');
            if (!settingsMain || !settingsMain.shadowRoot) return 'error: no settings-main';
            
            const basicPage = settingsMain.shadowRoot.querySelector('settings-basic-page');
            if (!basicPage || !basicPage.shadowRoot) return 'error: no settings-basic-page';
            
            const privacyPage = basicPage.shadowRoot.querySelector('settings-privacy-page');
            if (!privacyPage || !privacyPage.shadowRoot) return 'error: no settings-privacy-page';
            
            const privacyRoot = privacyPage.shadowRoot;
            
            // Step 1: Click "Delete browsing data" link to open the dialog
            const clearLink = privacyRoot.querySelector('#clearBrowsingData');
            if (!clearLink) return 'error: no #clearBrowsingData link';
            clearLink.click();
            
            // Wait for dialog to appear
            await sleep(2000);
            
            // Step 2: Find the dialog
            const dialog = privacyRoot.querySelector('#deleteBrowsingDataDialog') ||
                          privacyRoot.querySelector('cr-dialog[id="deleteBrowsingDataDialog"]');
            if (!dialog) return 'error: no #deleteBrowsingDataDialog';
            
            // Step 3: Select "All time" via the time picker
            const timePicker = dialog.querySelector('#timePicker') ||
                             dialog.querySelector('settings-clear-browsing-data-time-picker');
            if (timePicker) {
                // Try to set time range to "All time"
                const selectEl = timePicker.shadowRoot ? 
                    timePicker.shadowRoot.querySelector('select') ||
                    timePicker.shadowRoot.querySelector('cr-action-menu') : null;
                if (selectEl && selectEl.tagName === 'SELECT') {
                    selectEl.value = '4';
                    selectEl.dispatchEvent(new Event('change', {bubbles: true}));
                    await sleep(500);
                }
            }
            
            // Step 4: Make sure all checkboxes are checked
            const checkboxes = dialog.querySelectorAll('settings-checkbox');
            checkboxes.forEach(cb => {
                if (!cb.hasAttribute('checked')) {
                    cb.setAttribute('checked', '');
                    cb.click();
                }
            });
            await sleep(300);
            
            // Step 5: Click "Delete data" button
            const deleteBtn = dialog.querySelector('#deleteButton');
            if (!deleteBtn) return 'error: no #deleteButton';
            
            deleteBtn.click();
            await sleep(3000);
            
            return 'success: clicked Delete data button';
        })()
        """

        result = await page.evaluate(clear_script, timeout=30)
        logger.info(f"[ClearData] Settings page result: {result}")

        if result and 'success' in str(result):
            logger.info(f"[ClearData] ✓ Cleared via chrome://settings/clearBrowserData (port={port})")
            # Wait for Chrome to finish clearing
            await asyncio.sleep(2)
        else:
            logger.warning(f"[ClearData] Settings page clear may have failed: {result}")
            # Fallback: try Network commands
            try:
                await page.send("Network.enable")
                await page.send("Network.clearBrowserCache")
                await page.send("Network.clearBrowserCookies")
                logger.info(f"[ClearData] ✓ Fallback CDP Network clear done (port={port})")
            except Exception:
                pass

        return True
    except Exception as e:
        logger.warning(f"[ClearData] CDP clear failed (port={port}): {e}")
        return False
    finally:
        # Close or reset the tab (navigate back to about:blank if reused)
        if tab_id and cdp:
            try:
                if reused_tab:
                    # Navigate back to about:blank instead of closing
                    page = await cdp.attach_to_target(tab_id)
                    await page.send("Page.navigate", {"url": "about:blank"})
                    logger.info(f"[ClearData] ✓ Reset tab to about:blank")
                else:
                    await cdp.close_tab(tab_id)
                    logger.info(f"[ClearData] ✓ Closed settings tab")
            except Exception:
                pass
        try:
            await cdp.close()
        except Exception:
            pass


async def clear_all_data(profile_dirs: List[str], cdp_ports: List[int]):
    """
    Full clear of browsing data:
    1. CDP: clear cache + cookies for each running Chrome instance
    2. File system: clear history DB + cache directories for each profile
    """
    logger.info("=" * 60)
    logger.info("[ClearData] Starting scheduled clear browsing data...")
    logger.info(f"[ClearData] Profiles: {len(profile_dirs)}, CDP ports: {cdp_ports}")
    logger.info("=" * 60)

    # Step 1: CDP clear (cache + cookies)
    for port in cdp_ports:
        if port:
            await clear_browsing_data_cdp(port)

    # Step 2: File system clear (history + download history + cache files)
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
    logger.info(f"[ClearData] Scheduler started - interval: every {interval_minutes} minutes")

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            info = get_info_fn()
            profile_dirs = info.get("profile_dirs", [])
            cdp_ports = info.get("cdp_ports", [])

            if not profile_dirs:
                logger.debug("[ClearData] No profiles found, skipping")
                continue

            await clear_all_data(profile_dirs, cdp_ports)

        except Exception as e:
            logger.error(f"[ClearData] Scheduler error: {e}", exc_info=True)


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
    logger.info(f"[ClearData] Scheduler registered - will clear every {interval_minutes} min")


def stop_clear_data_scheduler():
    """Stop the periodic clear data background task."""
    global _clear_task
    if _clear_task and not _clear_task.done():
        _clear_task.cancel()
        _clear_task = None
        logger.info("[ClearData] Scheduler stopped")
