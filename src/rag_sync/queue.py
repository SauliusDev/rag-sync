from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from rag_sync.db import RagSyncDb
from rag_sync.models import JobKind

JobHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]


class PersistentJobQueue:
    def __init__(self, db: RagSyncDb):
        self.db = db
        self._handlers: dict[str, JobHandler] = {}
        self.paused = False

    def register(self, kind: JobKind, handler: JobHandler) -> None:
        self._handlers[kind.value] = handler

    def enqueue(
        self,
        kind: JobKind,
        source_file_id: int | None = None,
        profile_name: str | None = None,
    ) -> int:
        return self.db.create_job(
            kind=kind.value,
            source_file_id=source_file_id,
            profile_name=profile_name,
        )

    async def run_next(self) -> bool:
        if self.paused:
            return False
        job = self.db.next_queued_job()
        if job is None:
            return False
        handler = self._handlers.get(str(job["kind"]))
        if handler is None:
            self.db.update_job_status(
                int(job["id"]),
                "failed",
                progress=0,
                error_summary=f"no handler registered for job kind {job['kind']}",
            )
            return True
        self.db.update_job_status(int(job["id"]), "running", progress=0)
        try:
            await handler(job)
        except Exception as exc:
            self.db.update_job_status(
                int(job["id"]),
                "failed",
                progress=0,
                error_summary=str(exc),
            )
        else:
            self.db.update_job_status(int(job["id"]), "completed", progress=1)
        return True
