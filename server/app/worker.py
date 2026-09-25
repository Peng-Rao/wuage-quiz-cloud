"""进程内任务队列（P1）。

任务状态全部落库，服务重启后会重新排队未完成的任务。
生产环境换成 Redis + 独立 Worker 进程时，只需替换 enqueue 与消费循环。
"""

import asyncio
import logging

from sqlalchemy import select

from .db import ParseJob, SessionLocal
from .pipeline.run import run_job_safely

log = logging.getLogger(__name__)

CONCURRENCY = 2


class Worker:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.tasks: list[asyncio.Task] = []

    def enqueue(self, job_id: str) -> None:
        self.queue.put_nowait(job_id)

    async def _loop(self) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                await run_job_safely(job_id)
            finally:
                self.queue.task_done()

    def start(self) -> None:
        with SessionLocal() as s:
            pending = s.scalars(
                select(ParseJob.id).where(ParseJob.status.in_(["queued", "running"])).order_by(ParseJob.created_at)
            ).all()
        for job_id in pending:
            log.info("重新排队未完成的任务 %s", job_id)
            self.enqueue(job_id)
        self.tasks = [asyncio.create_task(self._loop()) for _ in range(CONCURRENCY)]

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


worker = Worker()
