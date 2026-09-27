"""SQLite → PostgreSQL 数据迁移。目标默认为另一个 SQLite 文件；在 PostgreSQL 上运行测试时，目标为临时新建的库。"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, func, select, text

from app.auth import as_utc
from app.db import AiUsage, Base, ParseJob, engine
from app.migrate_to_pg import copy_database

T0 = datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)


def _job(i: int, **kw) -> ParseJob:  # noqa: ANN003
    return ParseJob(id=f"mig{i}", school_id="demo", file_name=f"卷{i}.pdf", file_count=1, file_size=1, file_type="pdf",
                    file_keys=[], options={}, status="done", progress=100, stages=[], warnings=[], created_at=T0, **kw)


@pytest.fixture
def source(tmp_path):
    src = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    Base.metadata.create_all(src)
    with src.begin() as c:
        # 逐行插入：批量插入只按第一行的列名写入
        for job in (_job(1, meta={"subject": "数学"}, answer_task={"status": "done"}), _job(2, error="含\x00字符")):
            c.execute(ParseJob.__table__.insert().values(
                **{k: v for k, v in vars(job).items() if not k.startswith("_")}))
        c.execute(AiUsage.__table__.insert(), [
            {"id": 7, "school_id": "demo", "provider": "llm", "purpose": "compose", "model": "m", "created_at": T0},
        ])
    return src


@pytest.fixture
def target(tmp_path):
    if engine.dialect.name != "postgresql":
        yield create_engine(f"sqlite:///{tmp_path / 'new.db'}")
        return
    # PostgreSQL：在同一实例上临时建一个库，不影响其他测试共用的库
    name = f"{engine.url.database}_migrate"
    admin = create_engine(engine.url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        c.execute(text(f'CREATE DATABASE "{name}"'))
    dst = create_engine(engine.url.set(database=name), connect_args={"options": "-c timezone=UTC"})
    yield dst
    dst.dispose()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE "{name}"'))


def test_copy_preserves_rows_nulls_and_times(source, target):
    logs: list[str] = []
    copied = copy_database(source, target, log=logs.append)
    assert copied["parse_job"] == 2 and copied["ai_usage"] == 1
    assert any("NUL" in line for line in logs)
    with target.connect() as c:
        jobs = {r.id: r for r in c.execute(select(ParseJob.__table__))}
        assert jobs["mig1"].meta == {"subject": "数学"} and jobs["mig1"].answer_task == {"status": "done"}
        # JSON 列的空值仍是 SQL NULL，按 IS NULL 查询的逻辑不受影响
        assert c.scalar(select(func.count()).where(ParseJob.__table__.c.answer_task.is_(None))) == 1
        assert jobs["mig2"].error == "含字符"
        assert as_utc(jobs["mig1"].created_at) == T0
        if target.dialect.name == "postgresql":
            # 显式写入的自增主键之后，新记录的 id 接着往后排
            new_id = c.execute(AiUsage.__table__.insert().values(
                school_id="demo", provider="llm", purpose="x", model="m", created_at=T0)).inserted_primary_key[0]
            assert new_id == 8


def test_refuses_non_empty_target_unless_replace(source, target):
    copy_database(source, target, log=lambda _: None)
    with pytest.raises(SystemExit, match="已有数据"):
        copy_database(source, target, log=lambda _: None)
    assert copy_database(source, target, replace=True, log=lambda _: None)["parse_job"] == 2


def test_as_utc():
    assert as_utc(datetime(2026, 9, 1, 8, 30)) == T0
    assert as_utc(datetime(2026, 9, 1, 16, 30, tzinfo=timezone(timedelta(hours=8)))) == T0
