import asyncio

import pytest

from src.jobs import LocalJobQueue
from src.models import JobKind


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


def test_job_queue_rejects_invalid_max_active_jobs():
    with pytest.raises(ValueError, match="max_active_jobs"):
        LocalJobQueue(max_active_jobs=0)


def test_job_queue_marks_failed_job():
    async def work():
        raise RuntimeError("boom")

    queue = LocalJobQueue(max_active_jobs=1)
    job = queue.enqueue(JobKind.SCAN, work)

    asyncio.run(queue.run_until_idle())

    assert job.status == "failed"
    assert job.error == "boom"


def test_job_queue_lists_jobs_in_order():
    async def work():
        return "ok"

    queue = LocalJobQueue(max_active_jobs=1)
    first = queue.enqueue(JobKind.SCAN, work)
    second = queue.enqueue(JobKind.CONVERT, work)

    assert queue.list_jobs() == [first, second]
    assert [job.id for job in queue.list_jobs()] == [1, 2]


def test_job_queue_respects_batch_size():
    running = 0
    max_seen = 0

    async def work():
        nonlocal running, max_seen
        running += 1
        max_seen = max(max_seen, running)
        await asyncio.sleep(0)
        running -= 1

    queue = LocalJobQueue(max_active_jobs=2)
    for _ in range(5):
        queue.enqueue(JobKind.SCAN, work)

    asyncio.run(queue.run_until_idle())

    assert max_seen == 2
    assert all(job.status == "completed" for job in queue.list_jobs())


def test_job_queue_concurrent_runners_do_not_run_job_twice():
    runs = 0

    async def work():
        nonlocal runs
        runs += 1
        await asyncio.sleep(0)

    async def run_concurrently(queue: LocalJobQueue):
        await asyncio.gather(queue.run_until_idle(), queue.run_until_idle())

    queue = LocalJobQueue(max_active_jobs=1)
    job = queue.enqueue(JobKind.SCAN, work)

    asyncio.run(run_concurrently(queue))

    assert runs == 1
    assert job.status == "completed"
