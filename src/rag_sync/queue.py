from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from rag_sync.db import RagSyncDb
from rag_sync.models import JobKind

JobHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]


class JobCanceledError(RuntimeError):
    pass


class PersistentJobQueue:
    def __init__(self, db: RagSyncDb):
        self.db = db
        self._handlers: dict[str, JobHandler] = {}
        self.paused = False
        self.current_job_id: int | None = None
        self.cancel_requested_job_ids: set[int] = set()

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

    def request_cancel_running(self, job_id: int | None = None) -> bool:
        active_job_id = job_id if job_id is not None else self.current_job_id
        if active_job_id is None:
            return False
        self.cancel_requested_job_ids.add(int(active_job_id))
        self.paused = True
        return True

    def consume_cancel_request(self, job_id: int) -> bool:
        if job_id in self.cancel_requested_job_ids:
            self.cancel_requested_job_ids.discard(job_id)
            return True
        return False

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
        job_id = int(job["id"])
        self.current_job_id = job_id
        self.db.update_job_status(job_id, "running", progress=0)
        try:
            await handler(job)
            if self.consume_cancel_request(job_id):
                self.db.update_job_status(
                    job_id,
                    "canceled",
                    progress=0,
                    error_summary="killed by user",
                )
                return True
        except Exception as exc:
            if self.consume_cancel_request(job_id):
                self.db.update_job_status(
                    job_id,
                    "canceled",
                    progress=0,
                    error_summary="killed by user",
                )
            else:
                self.db.update_job_status(
                    job_id,
                    "failed",
                    progress=0,
                    error_summary=str(exc),
                )
        else:
            self.db.update_job_status(job_id, "completed", progress=1)
        finally:
            if self.current_job_id == job_id:
                self.current_job_id = None
        return True
