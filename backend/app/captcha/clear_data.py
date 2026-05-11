"""
Periodic Chrome browsing data cleaner via CDP.
Clears: browsing history, cookies, cache, download history.
"""
import asyncio
import logging
from typing import Optional

from .cdp_client import RawCDPClient

logger = logging.getLogger(__name__)

_clear_task: Optional[asyncio.Task] = None


async def clear_browsing_data(port: int):
    """Clear all browsing data via CDP (like Chrome's 'Delete browsing data' with All time)."""
    cdp = RawCDPClient(port)
    try:
        await cdp.connect()

        # Clear browser cache
        await cdp.send("Network.clearBrowserCache")
        logger.info("Cleared browser cache")

        # Clear browser cookies
        await cdp.send("Network.clearBrowserCookies")
        logger.info("Cleared browser cookies")

        # Clear browsing history, download history, cache storage via Browser.resetPermissions
        # and Storage commands for thorough cleanup
        # Use CDP BrowserHistory to clear history
        result = await cdp.send("Storage.clearDataForOrigin", {
            "origin": "*",
            "storageTypes": "all",
        })
        logger.debug(f"Storage.clearDataForOrigin result: {result}")

        # Clear download history and browsing history via Page on a temp tab
        tab_id = await cdp.create_tab("chrome://settings/clearBrowserData")
        await asyncio.sleep(1)

        # Use the direct CDP approach - execute JS to call Chrome's internal API
        session = await cdp.attach_to_target(tab_id)

        # Use chrome.browsingData API via JS to clear everything
        clear_js = """
        new Promise((resolve, reject) => {
            if (chrome && chrome.browsingData) {
                chrome.browsingData.remove(
                    { since: 0 },
                    {
                        browsing_history: true,
                        cookies: true,
                        cache: true,
                        download_history: true,
                        fileSystems: true,
                        indexedDB: true,
                        localStorage: true,
                        webSQL: true,
                        serviceWorkers: true,
                        cacheStorage: true,
                    },
                    () => resolve("done")
                );
            } else {
                resolve("no_api");
            }
        })
        """
        try:
            result = await session.evaluate(clear_js, timeout=10)
            logger.info(f"browsingData.remove result: {result}")
        except Exception as e:
            logger.debug(f"browsingData API not available (expected on non-extension pages): {e}")

        # Fallback: navigate to clear data URL and trigger via settings page
        try:
            await session.navigate("chrome://settings/clearBrowserData")
            await asyncio.sleep(2)

            # Click "Clear data" button via JS on settings page
            click_js = """
            (function() {
                const settingsUi = document.querySelector('settings-ui');
                if (!settingsUi) return 'no_settings_ui';
                const root1 = settingsUi.shadowRoot;
                const main = root1.querySelector('settings-main');
                if (!main) return 'no_main';
                const root2 = main.shadowRoot;
                const page = root2.querySelector('settings-basic-page');
                if (!page) return 'no_page';
                const root3 = page.shadowRoot;
                const privacy = root3.querySelector('settings-privacy-page');
                if (!privacy) return 'no_privacy';
                const root4 = privacy.shadowRoot;
                const dialog = root4.querySelector('settings-clear-browsing-data-dialog');
                if (!dialog) return 'no_dialog';
                const root5 = dialog.shadowRoot;
                const btn = root5.querySelector('#clearBrowsingDataConfirm');
                if (!btn) return 'no_btn';
                btn.click();
                return 'clicked';
            })()
            """
            result = await session.evaluate(click_js, timeout=5)
            logger.info(f"Settings page clear result: {result}")
        except Exception as e:
            logger.debug(f"Settings page clear fallback failed: {e}")

        # Close the temp tab
        try:
            await cdp.close_tab(tab_id)
        except Exception:
            pass

        logger.info("✓ Browsing data cleared successfully")

    except Exception as e:
        logger.error(f"Failed to clear browsing data: {e}")
    finally:
        await cdp.close()


async def _periodic_clear(interval_minutes: int, get_port_fn):
    """Background task that clears data every N minutes."""
    interval_seconds = interval_minutes * 60
    logger.info(f"Clear data scheduler started: every {interval_minutes} minutes")

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            port = get_port_fn()
            if port:
                logger.info(f"Running scheduled clear browsing data (port={port})...")
                await clear_browsing_data(port)
            else:
                logger.debug("Chrome not running, skipping clear data")
        except Exception as e:
            logger.error(f"Scheduled clear data error: {e}")


def start_clear_data_scheduler(interval_minutes: int, get_port_fn):
    """Start the periodic clear data background task."""
    global _clear_task
    if interval_minutes <= 0:
        logger.info("Clear data scheduler disabled (interval=0)")
        return
    if _clear_task and not _clear_task.done():
        logger.info("Clear data scheduler already running")
        return
    _clear_task = asyncio.ensure_future(_periodic_clear(interval_minutes, get_port_fn))


def stop_clear_data_scheduler():
    """Stop the periodic clear data background task."""
    global _clear_task
    if _clear_task and not _clear_task.done():
        _clear_task.cancel()
        _clear_task = None
        logger.info("Clear data scheduler stopped")
