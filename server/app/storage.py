"""对象存储抽象。P1 提供本地磁盘实现，生产环境替换为 OSS / S3 / MinIO（预签名直传）。"""

import re
import uuid
from pathlib import Path
from typing import Protocol

from .config import get_settings

_SAFE_KEY = re.compile(r"^[A-Za-z0-9._/-]+$")


class ObjectStore(Protocol):
    def new_upload_key(self, file_name: str) -> str: ...
    def upload_url(self, key: str) -> str: ...
    def public_url(self, key: str) -> str: ...
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def size(self, key: str) -> int: ...
    def path(self, key: str) -> Path: ...


class LocalStore:
    """文件存放在 data_dir/storage 下；上传和读取都经由本服务的 /api/files 接口。"""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def new_upload_key(self, file_name: str) -> str:
        ext = Path(file_name).suffix.lower()
        ext = ext if re.fullmatch(r"\.[a-z0-9]{1,5}", ext) else ""
        return f"uploads/{uuid.uuid4().hex}{ext}"

    def upload_url(self, key: str) -> str:
        return f"/api/files/{key}"

    def public_url(self, key: str) -> str:
        return f"/api/files/{key}"

    def path(self, key: str) -> Path:
        if not _SAFE_KEY.fullmatch(key) or ".." in key.split("/"):
            raise ValueError("非法的存储路径")
        return self.root / key

    def put(self, key: str, data: bytes) -> None:
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(p)

    def get(self, key: str) -> bytes:
        return self.path(key).read_bytes()

    def exists(self, key: str) -> bool:
        try:
            return self.path(key).is_file()
        except ValueError:
            return False

    def size(self, key: str) -> int:
        return self.path(key).stat().st_size


_store: ObjectStore | None = None


def get_store() -> ObjectStore:
    global _store
    if _store is None:
        _store = LocalStore(get_settings().data_dir / "storage")
    return _store
