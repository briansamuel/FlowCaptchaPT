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
    """Clear browsing history and download history by wiping Chrome SQLite files."""
    profile = Path(profile_dir)

    # Chrome stores history in "Default/History" or directly in profile root
    # depending on how the profile was set up
    history_locations = [
        profile / "Default" / "History",
        profile / "History",
    ]

    for history_file in history_locations:
        if history_file.exists():
            try:
                conn = sqlite3.connect(str(history_file))
                cursor = conn.cursor()
                # Clear browsing history
                cursor.execute("DELETE FROM urls")
                cursor.execute("DELETE FROM visits")
                cursor.execute("DELETE FROM visit_source")
                # Clear download history
                try:
                    cursor.execute("DELETE FROM downloads")
                    cursor.execute("DELETE FROM downloads_url_chains")
                except sqlite3.OperationalError:
                    pass  # Table might not exist
                conn.commit()
                conn.close()
                logger.info(f"[ClearData] ✓ Cleared history DB: {history_file}")
            except Exception as e:
                logger.warning(f"[ClearData] Could not clear {history_file}: {e}")
                # Fallback: try to delete the file entirely
                try:
                    history_file.unlink()
                    logger.info(f"[ClearData] ✓ Deleted history file: {history_file}")
                except Exception:
                    pass

    # Also clear the journal files
    for loc in history_locations:
        journal = Path(str(loc) + "-journal")
        if journal.exists():
            try:
                journal.unlink()
            except Exception:
                pass


def _clear_cache_dirs(profile_dir: str):
    """Clear cached images and files by removing Cache directories."""
    profile = Path(profile_dir)

    cache_dirs = [
        profile / "Default" / "Cache",
        profile / "Default" / "Code Cache",
        profile / "Default" / "GPUCache",
        profile / "Cache",
        profile / "Code Cache",
        profile / "GPUCache",
        profile / "ShaderCache",
    ]

    for cache_dir in cache_dirs:
        if cache_dir.exists() and cache_dir.is_dir():
            try:
                shutil.rmtree(str(cache_dir), ignore_errors=True)
                cache_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"[ClearData] ✓ Cleared cache dir: {cache_dir}")
            except Exception as e:
                logger.warning(f"[ClearData] Could not clear cache {cache_dir}: {e}")


async def clear_browsing_data_cdp(port: int) -> bool:
    """Clear cache and cookies via CDP commands (most reliable method).
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
    try:
        await cdp.connect()

        # Clear browser cache
        result = await cdp.send("Network.clearBrowserCache")
        if "error" not in result:
            logger.info(f"[ClearData] ✓ CDP: Browser cache cleared (port={port})")
        else:
            logger.warning(f"[ClearData] CDP clearBrowserCache error: {result.get('error')}")

        # Clear browser cookies
        result = await cdp.send("Network.clearBrowserCookies")
        if "error" not in result:
            logger.info(f"[ClearData] ✓ CDP: Browser cookies cleared (port={port})")
        else:
            logger.warning(f"[ClearData] CDP clearBrowserCookies error: {result.get('error')}")

        return True
    except Exception as e:
        logger.warning(f"[ClearData] CDP clear failed (port={port}): {e}")
        return False
    finally:
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
