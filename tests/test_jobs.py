import asyncio

from rag_sync.jobs import LocalJobQueue
from rag_sync.models import JobKind


def test_job_queue_runs_job():
    events: list[str] = []

    async def work():
        events.append("ran")
        return "ok"

    queue = LocalJobQueue(max_active_jobs=1)
    job = queue.enqueue(JobKind.SCAN, work)

    asyncio.run(queue.run_until_idle())

    assert job.status == "completed"
    assert job.result == "ok"
    assert events == ["ran"]
