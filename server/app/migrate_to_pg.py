"""把 SQLite 数据库中的数据复制到 DATABASE_URL 指向的 PostgreSQL。

    uv run python -m app.migrate_to_pg                       # 读取 DATA_DIR/app.db
    uv run python -m app.migrate_to_pg path/to/app.db
    docker compose run --rm app python -m app.migrate_to_pg  # Docker：读取数据卷中的 /data/app.db

- 目标库中已有数据时拒绝执行，避免重复导入；加 --replace 先清空目标库（会删除其中全部数据）。
- 按表的外键依赖顺序复制，全部在一个事务中完成：任何一张表失败则目标库保持原样。
- SQLite 中的时间不带时区（实际为 UTC），写入时补上 UTC；自增主键的序列同步到最大值。
- JSON 列中的空值保持为 SQL NULL（部分查询按 IS NULL 判断）；文本中的 NUL 字符（PostgreSQL 不允许）会被去掉并提示。
- 只复制当前模型中定义的表和列：源库缺少的新列取默认值（与在 SQLite 上启动时自动补列的结果一致）。
- 上传文件、页面图等仍在 DATA_DIR 中，不在数据库里，迁移后继续使用同一个 DATA_DIR 即可。
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, Engine, Integer, MetaData, create_engine, func, inspect, select, text

from .config import get_settings
from .db import Base

BATCH = 500


def _clean(v: Any, stripped: list[int]) -> Any:
    if isinstance(v, datetime):
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v
    if isinstance(v, str):
        if "\x00" in v:
            stripped[0] += 1
            return v.replace("\x00", "")
        return v
    if isinstance(v, list):
        return [_clean(x, stripped) for x in v]
    if isinstance(v, dict):
        return {k: _clean(x, stripped) for k, x in v.items()}
    return v


def _insert_tables() -> MetaData:
    """写入用的表定义：JSON 列的 None 写成 SQL NULL，而不是 JSON 的 null（默认行为）。"""
    meta = MetaData()
    for table in Base.metadata.sorted_tables:
        table = table.to_metadata(meta)
        for col in table.columns:
            if isinstance(col.type, JSON):
                col.type = JSON(none_as_null=True)
    return meta


def target_rows(dst: Engine) -> dict[str, int]:
    """目标库中各表的行数，只统计已存在且非空的表。"""
    existing = set(inspect(dst).get_table_names())
    with dst.connect() as conn:
        counts = {t.name: conn.scalar(select(func.count()).select_from(t)) or 0
                  for t in Base.metadata.sorted_tables if t.name in existing}
    return {k: v for k, v in counts.items() if v}


def copy_database(src: Engine, dst: Engine, *, replace: bool = False, log=print) -> dict[str, int]:  # noqa: ANN001
    """复制 src 中各表的数据到 dst，返回各表复制的行数。"""
    if not replace and (busy := target_rows(dst)):
        raise SystemExit("目标库中已有数据（" + "、".join(f"{k} {v} 行" for k, v in busy.items()) +
                         "）。确认要用 SQLite 的数据覆盖时加 --replace。")
    if replace:
        Base.metadata.drop_all(dst)
    Base.metadata.create_all(dst)

    src_tables = set(inspect(src).get_table_names())
    writable = _insert_tables()
    copied: dict[str, int] = {}
    stripped = [0]
    with src.connect() as sconn, dst.begin() as dconn:
        for table in Base.metadata.sorted_tables:  # 父表在前
            if table.name not in src_tables:
                log(f"  {table.name}：源库中没有该表，跳过")
                continue
            src_cols = {c["name"] for c in inspect(src).get_columns(table.name)}
            cols = [c for c in table.columns if c.name in src_cols]
            n = 0
            result = sconn.execution_options(yield_per=BATCH).execute(select(*cols))
            for chunk in result.partitions(BATCH):
                rows = [{k: _clean(v, stripped) for k, v in row._mapping.items()} for row in chunk]
                dconn.execute(writable.tables[table.name].insert(), rows)
                n += len(rows)
            copied[table.name] = n
            log(f"  {table.name}：{n} 行")
        if stripped[0]:
            log(f"  注意：{stripped[0]} 个文本值含 NUL 字符（PostgreSQL 不允许），已去掉")
        if dst.dialect.name == "postgresql":
            # 显式写入了自增主键，序列不会随之前进，需同步到当前最大值
            for table in Base.metadata.sorted_tables:
                for col in table.primary_key.columns:
                    if isinstance(col.type, Integer) and col.autoincrement in (True, "auto"):
                        dconn.execute(text(
                            f"SELECT setval(pg_get_serial_sequence('{table.name}', '{col.name}'), "
                            f"COALESCE(MAX({col.name}), 1), MAX({col.name}) IS NOT NULL) FROM {table.name}"))
    return copied


def main() -> None:
    parser = argparse.ArgumentParser(description="把 SQLite 数据库中的数据复制到 DATABASE_URL 指向的 PostgreSQL")
    parser.add_argument("sqlite", nargs="?", help="SQLite 数据库文件，默认 DATA_DIR/app.db")
    parser.add_argument("--replace", action="store_true", help="先清空目标库（删除其中全部数据）再导入")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.database_url or settings.db_url.startswith("sqlite"):
        raise SystemExit("请先把 DATABASE_URL 设为 PostgreSQL 地址，如 postgresql+psycopg://user:pass@host:5432/fg_quiz")
    path = Path(args.sqlite) if args.sqlite else settings.data_dir / "app.db"
    if not path.is_file():
        raise SystemExit(f"找不到 SQLite 数据库：{path}")

    src = create_engine(f"sqlite:///{path}")
    dst = create_engine(settings.db_url, connect_args={"options": "-c timezone=UTC"})
    print(f"从 {path} 复制到 {dst.url.render_as_string(hide_password=True)}")
    copied = copy_database(src, dst, replace=args.replace)
    print(f"完成：{len(copied)} 张表，共 {sum(copied.values())} 行。启动服务时会自动补齐缺少的列、执行一次性数据迁移。")


if __name__ == "__main__":
    sys.exit(main())
