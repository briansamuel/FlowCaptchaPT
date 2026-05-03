"""Async captcha job queue."""
from __future__ import annotations
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional

logger = logging.getLogger(__name__)

QUEUE_TIMEOUT = 120
POLL_INTERVAL = 3
CLEANUP_AFTER = 300


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class CaptchaJob:
    id: str
    action: str
    status: JobStatus = JobStatus.PENDING
    token: Optional[str] = None
    error: Optional[str] = None
    api_key_id: Optional[str] = None
    client_ip: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None

    @property
    def elapsed_ms(self) -> int:
        end = self.completed_at or time.time()
        return int((end - self.created_at) * 1000)

    def to_dict(self) -> dict:
        return {
            "job_id": self.id,
            "status": self.status.value,
            "token": self.token,
            "error": self.error,
            "success": self.status == JobStatus.COMPLETED,
            "elapsed_ms": self.elapsed_ms,
        }


class CaptchaJobQueue:
    def __init__(self):
        self._jobs: Dict[str, CaptchaJob] = {}
        self._cleanup_task: Optional[asyncio.Task] = None

    def submit(self, action: str, api_key_id: str = None, client_ip: str = None) -> CaptchaJob:
        job = CaptchaJob(
            id=str(uuid.uuid4()),
            action=action,
            api_key_id=api_key_id,
            client_ip=client_ip,
        )
        self._jobs[job.id] = job
        logger.info(f"Job {job.id[:8]} queued [{action}]")
        self._ensure_cleanup()
        return job

    def get(self, job_id: str) -> Optional[CaptchaJob]:
        return self._jobs.get(job_id)

    @property
    def stats(self) -> dict:
        counts = {}
        for j in self._jobs.values():
            counts[j.status.value] = counts.get(j.status.value, 0) + 1
        return {"total": len(self._jobs), **counts}

    async def run_worker(self, job: CaptchaJob, captcha_service):
        """Background worker: wait for service availability, then solve."""
        deadline = job.created_at + QUEUE_TIMEOUT
        job.status = JobStatus.PROCESSING

        while time.time() < deadline:
            result = await captcha_service.get_token(job.action)

            if result.error and "Cooldown" in result.error:
                await asyncio.sleep(POLL_INTERVAL)
                continue

            if result.token:
                job.status = JobStatus.COMPLETED
                job.token = result.token
            else:
                job.status = JobStatus.FAILED
                job.error = result.error

            job.completed_at = time.time()
            logger.info(f"Job {job.id[:8]} -> {job.status.value} ({job.elapsed_ms}ms)")
            return

        job.status = JobStatus.TIMEOUT
        job.error = f"Timeout after {QUEUE_TIMEOUT}s"
        job.completed_at = time.time()

    def _ensure_cleanup(self):
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.ensure_future(self._cleanup_loop())

    async def _cleanup_loop(self):
        while True:
            await asyncio.sleep(60)
            now = time.time()
            expired = [
                jid for jid, j in self._jobs.items()
                if j.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.TIMEOUT)
                and j.completed_at and (now - j.completed_at) > CLEANUP_AFTER
            ]
            for jid in expired:
                del self._jobs[jid]
            if not self._jobs:
                break


job_queue = CaptchaJobQueue()
