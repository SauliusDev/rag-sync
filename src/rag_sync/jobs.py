from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from rag_sync.models import JobKind


@dataclass
class QueuedJob:
    id: int
    kind: JobKind
    work: Callable[[], Awaitable[Any]]
    status: str = "queued"
    result: Any = None
    error: str = ""
    events: list[str] = field(default_factory=list)


class LocalJobQueue:
    def __init__(self, max_active_jobs: int = 2):
        if max_active_jobs < 1:
            raise ValueError("max_active_jobs must be >= 1")
        self.max_active_jobs = max_active_jobs
        self._jobs: list[QueuedJob] = []
        self._next_id = 1
        self._runner_lock = asyncio.Lock()

    def enqueue(self, kind: JobKind, work: Callable[[], Awaitable[Any]]) -> QueuedJob:
        job = QueuedJob(id=self._next_id, kind=kind, work=work)
        self._next_id += 1
        self._jobs.append(job)
        return job

    def list_jobs(self) -> list[QueuedJob]:
        return list(self._jobs)

    async def _run_one(self, job: QueuedJob) -> None:
        job.status = "running"
        try:
            job.result = await job.work()
            job.status = "completed"
        except Exception as exc:
            job.error = str(exc)
            job.status = "failed"

    async def run_until_idle(self) -> None:
        async with self._runner_lock:
            pending = [job for job in self._jobs if job.status == "queued"]
            while pending:
                batch = pending[: self.max_active_jobs]
                await asyncio.gather(*(self._run_one(job) for job in batch))
                pending = [job for job in self._jobs if job.status == "queued"]
