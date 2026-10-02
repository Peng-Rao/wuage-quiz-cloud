"""把本地存储（DATA_DIR/storage）中的文件上传到对象存储（OSS / COS），切换 STORAGE_BACKEND 前执行一次。

    uv run python -m app.migrate_storage --to cos            # 上传 COS 中还没有的文件
    uv run python -m app.migrate_storage --to cos --dry-run  # 只统计，不上传

--to 省略时使用 STORAGE_BACKEND 的值（须为 oss 或 cos）。
Docker 部署：docker compose exec app python -m app.migrate_storage --to cos（容器内 DATA_DIR 为 /data）。
数据库中保存的是存储 key，迁移后无需修改数据库。
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor

from .config import get_settings
from .storage import CosStore, OssStore


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to", choices=["oss", "cos"], help="目标对象存储，默认取 STORAGE_BACKEND")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    settings = get_settings()
    target = args.to or settings.storage_backend
    if target not in ("oss", "cos"):
        sys.exit("请用 --to oss 或 --to cos 指定目标对象存储")
    root = settings.data_dir / "storage"
    if not root.is_dir():
        sys.exit(f"本地存储目录不存在：{root}")
    store = OssStore(settings) if target == "oss" else CosStore(settings)
    files = [p for p in root.rglob("*") if p.is_file() and not p.name.endswith(".part")]
    print(f"本地文件 {len(files)} 个，目标 {target}://{store.bucket}/{store.prefix}")

    def one(p) -> str:  # noqa: ANN001
        key = p.relative_to(root).as_posix()
        if store.exists(key):
            return "skip"
        if not args.dry_run:
            store.put(key, p.read_bytes())
        return "put"

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(one, files))
    verb = "待上传" if args.dry_run else "已上传"
    print(f"{verb} {results.count('put')} 个，{target.upper()} 中已存在 {results.count('skip')} 个")


if __name__ == "__main__":
    main()
