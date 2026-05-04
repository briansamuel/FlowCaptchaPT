"""
Raw CDP client - connects to Chrome via WebSocket.
No Playwright, no Runtime.enable, no automation detection.
Only sends minimal CDP commands needed for token extraction.
"""
from __future__ import annotations
import asyncio
import json
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

SITE_KEY = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV"
TARGET_URL = "https://labs.google/"


class RawCDPClient:
    """Minimal CDP client that avoids automation detection."""

    def __init__(self, port: int):
        self.port = port
        self._msg_id = 0
        self._ws = None
        self._pending = {}
        self._events = asyncio.Queue()
        self._reader_task = None

    async def _get_ws_url(self) -> str:
        """Get browser websocket URL from /json/version."""
        url = f"http://127.0.0.1:{self.port}/json/version"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                data = await resp.json()
                return data["webSocketDebuggerUrl"]

    async def _get_page_ws(self, target_id: str) -> str:
        """Get page websocket URL."""
        url = f"http://127.0.0.1:{self.port}/json"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                targets = await resp.json()
                for t in targets:
                    if t.get("id") == target_id:
                        return t["webSocketDebuggerUrl"]
        raise RuntimeError(f"Target {target_id} not found")

    async def connect(self):
        """Connect to browser-level websocket."""
        ws_url = await self._get_ws_url()
        logger.info(f"Connecting to browser WS: {ws_url}")
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(ws_url, max_msg_size=50*1024*1024)
        self._reader_task = asyncio.ensure_future(self._reader())

    async def _reader(self):
        """Read messages from websocket."""
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    msg_id = data.get("id")
                    if msg_id and msg_id in self._pending:
                        self._pending[msg_id].set_result(data)
                    else:
                        await self._events.put(data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        except Exception as e:
            logger.debug(f"WS reader ended: {e}")

    async def send(self, method: str, params: dict = None, timeout: float = 30) -> dict:
        """Send CDP command and wait for response."""
        self._msg_id += 1
        msg_id = self._msg_id
        msg = {"id": msg_id, "method": method}
        if params:
            msg["params"] = params

        future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future

        await self._ws.send_json(msg)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        finally:
            self._pending.pop(msg_id, None)

    async def wait_event(self, method: str, timeout: float = 60) -> dict:
        """Wait for a specific CDP event."""
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            try:
                remaining = deadline - asyncio.get_running_loop().time()
                event = await asyncio.wait_for(self._events.get(), timeout=max(0.1, remaining))
                if event.get("method") == method:
                    return event
            except asyncio.TimeoutError:
                break
        raise TimeoutError(f"Timeout waiting for {method}")

    async def close(self):
        """Disconnect cleanly."""
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._reader_task), timeout=2)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
        # Allow event loop to process cleanup
        await asyncio.sleep(0.1)

    async def create_tab(self, url: str = "about:blank") -> str:
        """Create new tab, return targetId."""
        result = await self.send("Target.createTarget", {"url": url})
        target_id = result["result"]["targetId"]
        logger.info(f"Created tab: {target_id}")
        return target_id

    async def close_tab(self, target_id: str):
        """Close a tab."""
        await self.send("Target.closeTarget", {"targetId": target_id})

    async def attach_and_navigate(self, target_id: str, url: str) -> "PageSession":
        """Attach to target and navigate. Returns a PageSession for JS execution."""
        result = await self.send("Target.attachToTarget", {
            "targetId": target_id,
            "flatten": True,
        })
        session_id = result["result"]["sessionId"]
        return PageSession(self, session_id, target_id, url)

    async def attach_to_target(self, target_id: str) -> "PageSession":
        """Attach to target. Returns a PageSession for JS execution."""
        result = await self.send("Target.attachToTarget", {
            "targetId": target_id,
            "flatten": True,
        })
        session_id = result["result"]["sessionId"]
        return PageSession(self, session_id, target_id, "")


class PageSession:
    """Minimal page session - only navigate + evaluate JS. No Runtime.enable."""

    def __init__(self, client: RawCDPClient, session_id: str, target_id: str, url: str):
        self.client = client
        self.session_id = session_id
        self.target_id = target_id
        self._url = url

    async def send(self, method: str, params: dict = None, timeout: float = 30) -> dict:
        """Send command to this specific session."""
        self.client._msg_id += 1
        msg_id = self.client._msg_id
        msg = {
            "id": msg_id,
            "method": method,
            "sessionId": self.session_id,
        }
        if params:
            msg["params"] = params

        future = asyncio.get_running_loop().create_future()
        self.client._pending[msg_id] = future
        await self.client._ws.send_json(msg)

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self.client._pending.pop(msg_id, None)

    async def navigate(self, url: str):
        """Navigate without enabling Page domain (no Page.enable)."""
        result = await self.send("Page.navigate", {"url": url}, timeout=120)
        if "error" in result:
            raise RuntimeError(f"Navigate failed: {result['error']}")
        # Wait for load by polling document.readyState
        for _ in range(120):
            state = await self.evaluate("document.readyState")
            if state in ("complete", "interactive"):
                return
            await asyncio.sleep(0.5)

    async def evaluate(self, expression: str, timeout: float = 30):
        """Execute JS via Runtime.evaluate (single call, no Runtime.enable)."""
        result = await self.send("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        }, timeout=timeout)

        if "error" in result:
            raise RuntimeError(f"Evaluate error: {result['error']}")

        remote_obj = result.get("result", {}).get("result", {})
        if remote_obj.get("type") == "undefined":
            return None
        return remote_obj.get("value")
