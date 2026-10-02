"""Redis 任务队列：去重、多 Worker 分派、失联接手、停止交还、补排。

默认使用 fakeredis；设置 TEST_REDIS_URL（如 redis://127.0.0.1:6379/15）时连接真实 Redis，测试会清空其中本测试前缀的键。
"""

import asyncio
import os
import uuid

import pytest

from app import worker as w
from app.db import ParseJob, SessionLocal
from app.services import current_school

REAL = os.environ.get("TEST_REDIS_URL", "")
# 其余用例把补排替换为空，这里保留原函数
UNFINISHED = w.unfinished_tasks
BLOCK_MS = w.BLOCK_MS


@pytest.fixture
def clients():
    prefix = f"test-{uuid.uuid4().hex[:8]}"
    if REAL:
        import redis
        import redis.asyncio
        sync, factory = redis.Redis.from_url(REAL, decode_responses=True), lambda: redis.asyncio.Redis.from_url(REAL, decode_responses=True)
    else:
        import fakeredis
        server = fakeredis.FakeServer()
        sync = fakeredis.FakeRedis(server=server, decode_responses=True)
        factory = lambda: fakeredis.FakeAsyncRedis(server=server, decode_responses=True)  # noqa: E731
    yield w.RedisQueue(sync, prefix), factory
    for k in sync.scan_iter(f"{prefix}:*"):
        sync.delete(k)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(w, "BLOCK_MS", 50)
    monkeypatch.setattr(w, "HEARTBEAT_S", 0.05)
    monkeypatch.setattr(w, "unfinished_tasks", lambda: [])


def recorder(done: list, delay: float = 0.0):  # noqa: ANN201
    async def run(kind: str, job_id: str) -> None:
        await asyncio.sleep(delay)
        done.append((kind, job_id))
    return run


async def until(cond, timeout: float = 5.0) -> None:  # noqa: ANN001
    t = asyncio.get_running_loop().time()
    while not cond():
        assert asyncio.get_running_loop().time() - t < timeout, "等待超时"
        await asyncio.sleep(0.02)


def test_enqueue_is_deduplicated(clients):
    q, _ = clients
    assert q.put("parse", "j1") is True
    assert q.put("parse", "j1") is False       # 已在队列中
    assert q.put("answer", "j1") is True       # 不同类型的任务互不影响
    assert q.r.xlen(q.stream) == 2


async def test_two_workers_share_tasks_and_clean_up(clients):
    q, factory = clients
    done: list = []
    workers = [w.RedisConsumer(q, factory(), 2, name=f"w{i}", task_runner=recorder(done, 0.05)) for i in range(2)]
    for c in workers:
        await c.start()
    for i in range(8):
        q.put("parse", f"j{i}")
    await until(lambda: len(done) == 8)
    for c in workers:
        await c.stop()
    assert sorted(j for _, j in done) == [f"j{i}" for i in range(8)]   # 每个任务恰好执行一次
    assert q.r.xlen(q.stream) == 0 and not list(q.r.scan_iter(f"{q.prefix}:queued:*"))
    # 执行完后可以再次入队（如失败后重试）
    assert q.put("parse", "j0") is True


async def test_failed_task_is_acknowledged(clients):
    q, factory = clients

    async def boom(kind: str, job_id: str) -> None:
        raise RuntimeError("解析出错")

    c = w.RedisConsumer(q, factory(), 1, name="w", task_runner=boom)
    await c.start()
    q.put("parse", "bad")
    await until(lambda: q.r.xlen(q.stream) == 0)
    await c.stop()
    assert q.put("parse", "bad") is True


async def test_task_of_lost_worker_is_reclaimed(clients, monkeypatch):
    q, factory = clients
    monkeypatch.setattr(w, "RECLAIM_IDLE_MS", 100)
    q.put("parse", "orphan")
    dead = factory()
    await dead.xgroup_create(q.stream, w.GROUP, id="0", mkstream=True)
    await dead.xreadgroup(w.GROUP, "dead-worker", {q.stream: ">"}, count=1)   # 取走后未确认即「崩溃」
    done: list = []
    c = w.RedisConsumer(q, factory(), 1, name="live", task_runner=recorder(done))
    await c.start()
    await until(lambda: done == [("parse", "orphan")])
    await c.stop()
    assert q.r.xpending(q.stream, w.GROUP)["pending"] == 0


async def test_running_task_is_not_reclaimed_while_alive(clients, monkeypatch):
    q, factory = clients
    monkeypatch.setattr(w, "RECLAIM_IDLE_MS", 200)
    done: list = []
    slow = w.RedisConsumer(q, factory(), 1, name="slow", task_runner=recorder(done, 0.8))   # 远超 RECLAIM_IDLE_MS
    other = w.RedisConsumer(q, factory(), 1, name="other", task_runner=recorder(done))
    await slow.start()
    q.put("parse", "long")
    await until(lambda: slow.in_flight)
    await until(lambda: q.info()["busy"] == 1, timeout=1)   # 开始执行即更新状态，不等下一次心跳
    await other.start()
    await until(lambda: len(done) == 1)
    await asyncio.sleep(0.2)
    await slow.stop()
    await other.stop()
    assert done == [("parse", "long")]    # 续约期间不会被其他 Worker 重复执行


async def test_stop_hands_back_task(clients):
    q, factory = clients
    done: list = []
    first = w.RedisConsumer(q, factory(), 1, name="first", task_runner=recorder(done, 30))
    await first.start()
    q.put("parse", "handoff")
    await until(lambda: first.in_flight)
    await first.stop()                     # 未执行完就停止：任务立即交还
    second = w.RedisConsumer(q, factory(), 1, name="second", task_runner=recorder(done))
    await second.start()
    await until(lambda: done == [("parse", "handoff")], timeout=2)
    await second.stop()


async def test_start_requeues_unfinished_jobs_once(clients, monkeypatch):
    q, factory = clients
    monkeypatch.setattr(w, "unfinished_tasks", lambda: [("parse", "a"), ("answer", "b")])
    q.put("parse", "a")                    # 已在队列中：不重复
    blocker = asyncio.Event()

    async def hold(kind: str, job_id: str) -> None:
        await blocker.wait()

    c = w.RedisConsumer(q, factory(), 1, name="w", task_runner=hold)
    await c.start()
    assert q.r.xlen(q.stream) == 2
    blocker.set()
    await until(lambda: q.r.xlen(q.stream) == 0)
    await c.stop()


def test_unfinished_tasks_from_database():
    jid = "q" + uuid.uuid4().hex[:15]
    with SessionLocal() as s:
        s.add(ParseJob(id=jid, school_id=current_school(), file_name="t.pdf", file_count=1, file_size=1, file_type="pdf",
                       file_keys=[], options={}, status="queued", progress=0, stages=[], warnings=[],
                       answer_task={"status": "running"}))
        s.commit()
    try:
        got = UNFINISHED()
        assert ("parse", jid) in got and ("answer", jid) in got
    finally:
        with SessionLocal() as s:
            s.delete(s.get(ParseJob, jid))
            s.commit()


def test_info_counts_live_workers(clients):
    q, _ = clients
    q.r.set(f"{q.prefix}:worker:h1-1", '{"concurrency": 2, "busy": 1}', ex=60)
    q.r.set(f"{q.prefix}:worker:h2-1", '{"concurrency": 3, "busy": 0}', ex=60)
    q.put("parse", "x")
    info = q.info()
    assert (info["workers"], info["slots"], info["busy"]) == (2, 5, 1)


def test_async_client_timeout_longer_than_block(monkeypatch):
    # 读取超时不长于阻塞等待时，空闲取任务会报超时（redis-py 默认 5 秒，恰与 BLOCK_MS 相同）
    monkeypatch.setattr(w, "BLOCK_MS", BLOCK_MS)
    c = w.async_client("redis://localhost:6379/0")
    assert c.connection_pool.connection_kwargs["socket_timeout"] >= BLOCK_MS / 1000 + 5


# ---------------- 并发上限与线程池 ----------------

def test_worker_concurrency_range():
    from pydantic import ValidationError

    from app.config import Settings

    assert Settings(worker_concurrency=10).worker_concurrency == 10
    for bad in (0, 11):
        with pytest.raises(ValidationError):
            Settings(worker_concurrency=bad)


async def test_size_executor_scales_with_concurrency():
    from app.pipeline.run import PUT_CONCURRENCY
    from app.worker import size_executor

    loop = asyncio.get_running_loop()
    size_executor(10)
    seen: set[str] = set()

    def slow() -> None:
        import threading
        import time
        seen.add(threading.current_thread().name)
        time.sleep(0.2)

    # 默认线程池（CPU 数 + 4）放不下的并行量也能同时执行
    n = 10 * PUT_CONCURRENCY
    await asyncio.gather(*(loop.run_in_executor(None, slow) for _ in range(n)))
    assert len(seen) == n and all(x.startswith("fg-quiz") for x in seen)
