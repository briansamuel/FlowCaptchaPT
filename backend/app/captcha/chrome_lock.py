"""
Shared Chrome lock - prevents clear data and captcha extraction from running simultaneously.
This avoids overwhelming Chrome with concurrent operations that cause VPS stuck.
"""
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# RWLock-like: multiple captcha can run concurrently, but clear data needs exclusive access
_clear_lock: Optional[asyncio.Lock] = None
_active_extractions: int = 0
_extraction_lock: Optional[asyncio.Lock] = None


def _ensure_locks():
    global _clear_lock, _extraction_lock
    if _clear_lock is None:
        _clear_lock = asyncio.Lock()
    if _extraction_lock is None:
        _extraction_lock = asyncio.Lock()


async def acquire_for_clear():
    """Acquire lock for clear data operation. Waits until no active extractions."""
    _ensure_locks()
    await _clear_lock.acquire()
    # Wait for active extractions to finish (max 30s)
    for _ in range(60):
        if _active_extractions == 0:
            return
        await asyncio.sleep(0.5)
    logger.warning("[ChromeLock] Proceeding with clear despite active extractions")


def release_for_clear():
    """Release clear data lock."""
    _ensure_locks()
    if _clear_lock.locked():
        _clear_lock.release()


async def acquire_for_extraction() -> bool:
    """Try to acquire for captcha extraction. Returns False if clear is running."""
    _ensure_locks()
    global _active_extractions
    if _clear_lock.locked():
        # Clear is running, wait briefly
        try:
            await asyncio.wait_for(_clear_lock.acquire(), timeout=5)
            _clear_lock.release()
        except asyncio.TimeoutError:
            return False
    async with _extraction_lock:
        _active_extractions += 1
    return True


async def release_for_extraction():
    """Release extraction counter."""
    _ensure_locks()
    global _active_extractions
    async with _extraction_lock:
        _active_extractions = max(0, _active_extractions - 1)


def is_clear_running() -> bool:
    """Check if clear data is currently running."""
    _ensure_locks()
    return _clear_lock.locked()
