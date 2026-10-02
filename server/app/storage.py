"""对象存储抽象：本地磁盘（LocalStore）、阿里云 OSS（OssStore）或腾讯云 COS（CosStore），由 STORAGE_BACKEND 选择。

- 本地：上传和读取都经由本服务的 /api/files 接口。
- OSS / COS：浏览器用预签名地址直传对象存储；读取先经 /api/files 校验权限，再重定向到短时有效的签名地址。
  Bucket 保持私有，不开放公共读。
"""

import datetime
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Protocol

from .config import Settings, get_settings

log = logging.getLogger(__name__)
_SAFE_KEY = re.compile(r"^[A-Za-z0-9._/-]+$")


def check_key(key: str) -> str:
    if not _SAFE_KEY.fullmatch(key) or ".." in key.split("/"):
        raise ValueError("非法的存储路径")
    return key


def normalize_prefix(prefix: str) -> str:
    p = prefix.strip("/")
    return p + "/" if p else ""


def new_upload_key(file_name: str) -> str:
    ext = Path(file_name).suffix.lower()
    ext = ext if re.fullmatch(r"\.[a-z0-9]{1,5}", ext) else ""
    return f"uploads/{uuid.uuid4().hex}{ext}"


class ObjectStore(Protocol):
    def upload_ticket(self, key: str, content_type: str) -> tuple[str, dict[str, str]]:
        """浏览器上传地址与必须携带的请求头。"""
        ...

    def public_url(self, key: str) -> str: ...
    def signed_url(self, key: str, expires_s: int) -> str | None:
        """外部可直接访问的临时地址；本地存储没有，返回 None。"""
        ...

    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def size(self, key: str) -> int: ...


class LocalStore:
    """文件存放在 data_dir/storage 下。"""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def upload_ticket(self, key: str, content_type: str) -> tuple[str, dict[str, str]]:  # noqa: ARG002
        return f"/api/files/{key}", {}

    def public_url(self, key: str) -> str:
        return f"/api/files/{key}"

    def signed_url(self, key: str, expires_s: int) -> str | None:  # noqa: ARG002
        return None

    def path(self, key: str) -> Path:
        return self.root / check_key(key)

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


class OssStore:
    """阿里云 OSS。对象名为 OSS_PREFIX + key，数据库中仍只保存 key，切换存储或前缀不影响已有数据。"""

    def __init__(self, s: Settings, client=None):  # noqa: ANN001
        if not (s.oss_bucket and s.oss_region and s.oss_access_key_id and s.oss_access_key_secret):
            raise RuntimeError("STORAGE_BACKEND=oss 需要配置 OSS_BUCKET、OSS_REGION、OSS_ACCESS_KEY_ID、OSS_ACCESS_KEY_SECRET")
        import alibabacloud_oss_v2 as oss

        self._oss = oss
        self.bucket = s.oss_bucket
        self.prefix = normalize_prefix(s.oss_prefix)
        if client is None:
            cfg = oss.config.load_default()
            cfg.credentials_provider = oss.credentials.StaticCredentialsProvider(
                s.oss_access_key_id, s.oss_access_key_secret)
            cfg.region = s.oss_region
            if s.oss_endpoint:
                cfg.endpoint = s.oss_endpoint
            client = oss.Client(cfg)
        self.client = client
        # 签名地址给浏览器和大模型使用，须为公网地址；服务端读写可走内网 Endpoint（OSS_ENDPOINT）
        self.signer = client
        if s.oss_public_endpoint:
            cfg = oss.config.load_default()
            cfg.credentials_provider = oss.credentials.StaticCredentialsProvider(
                s.oss_access_key_id, s.oss_access_key_secret)
            cfg.region = s.oss_region
            cfg.endpoint = s.oss_public_endpoint
            self.signer = oss.Client(cfg)
        self.upload_expires = datetime.timedelta(seconds=s.storage_upload_expires)

    def _name(self, key: str) -> str:
        return self.prefix + check_key(key)

    def upload_ticket(self, key: str, content_type: str) -> tuple[str, dict[str, str]]:
        # 禁止覆盖：签名地址在有效期内重复使用也不能替换已上传的文件
        r = self.signer.presign(self._oss.PutObjectRequest(
            bucket=self.bucket, key=self._name(key), content_type=content_type or None, forbid_overwrite=True,
        ), expires=self.upload_expires)
        return r.url, {str(k): str(v) for k, v in (r.signed_headers or {}).items()}

    def public_url(self, key: str) -> str:
        return f"/api/files/{key}"

    def signed_url(self, key: str, expires_s: int) -> str | None:
        r = self.signer.presign(self._oss.GetObjectRequest(bucket=self.bucket, key=self._name(key)),
                                expires=datetime.timedelta(seconds=expires_s))
        return r.url

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(self._oss.PutObjectRequest(bucket=self.bucket, key=self._name(key), body=data))

    def get(self, key: str) -> bytes:
        r = self.client.get_object(self._oss.GetObjectRequest(bucket=self.bucket, key=self._name(key)))
        with r.body as body:
            return body.read()

    def exists(self, key: str) -> bool:
        try:
            name = self._name(key)
        except ValueError:
            return False
        return self.client.is_object_exist(self.bucket, name)

    def size(self, key: str) -> int:
        r = self.client.head_object(self._oss.HeadObjectRequest(bucket=self.bucket, key=self._name(key)))
        return int(r.content_length or 0)


class CosStore:
    """腾讯云 COS。对象名为 COS_PREFIX + key，数据库中仍只保存 key。

    默认域名 <bucket>.cos.<region>.myqcloud.com 在同地域的腾讯云服务器上自动解析为内网地址，
    在外网解析为公网地址，因此服务端读写与签名地址共用同一个客户端。
    """

    def __init__(self, s: Settings, client=None):  # noqa: ANN001
        if not (s.cos_bucket and s.cos_region and s.cos_secret_id and s.cos_secret_key):
            raise RuntimeError("STORAGE_BACKEND=cos 需要配置 COS_BUCKET、COS_REGION、COS_SECRET_ID、COS_SECRET_KEY")
        self.bucket = s.cos_bucket
        self.prefix = normalize_prefix(s.cos_prefix)
        if client is None:
            from qcloud_cos import CosConfig, CosS3Client

            # 连接池按并发放大：每个解析任务最多同时上传 PUT_CONCURRENCY 张图
            client = CosS3Client(CosConfig(Region=s.cos_region, SecretId=s.cos_secret_id, SecretKey=s.cos_secret_key,
                                           Scheme="https", Timeout=s.cos_timeout, PoolConnections=32, PoolMaxSize=32))
        self.client = client
        self.upload_expires = s.storage_upload_expires
        self.attempts = max(1, s.cos_attempts)

    def _name(self, key: str) -> str:
        return self.prefix + check_key(key)

    def upload_ticket(self, key: str, content_type: str) -> tuple[str, dict[str, str]]:
        # 签名包含这些头，浏览器须原样携带；禁止覆盖：签名地址在有效期内重复使用也不能替换已上传的文件
        headers = {"x-cos-forbid-overwrite": "true"}
        if content_type:
            headers["Content-Type"] = content_type
        url = self.client.get_presigned_url(Bucket=self.bucket, Key=self._name(key), Method="PUT",
                                            Expired=self.upload_expires, Headers=dict(headers))  # SDK 会往字典里写 Authorization
        return url, headers

    def public_url(self, key: str) -> str:
        return f"/api/files/{key}"

    def signed_url(self, key: str, expires_s: int) -> str | None:
        return self.client.get_presigned_url(Bucket=self.bucket, Key=self._name(key), Method="GET", Expired=expires_s)

    def _retry(self, op: str, fn):  # noqa: ANN001, ANN202
        """网络错误（连接 / 读取超时、连接重置等）重试，含读取响应体；COS 返回的业务错误（404、403 等）不重试。"""
        from qcloud_cos.cos_exception import CosServiceError

        for attempt in range(1, self.attempts + 1):
            try:
                return fn()
            except CosServiceError:
                raise
            except Exception as e:  # noqa: BLE001 — CosClientError、requests / urllib3 超时、OSError 等
                if attempt == self.attempts:
                    raise
                log.warning("COS %s 失败（第 %d 次），重试：%s", op, attempt, str(e)[:200])
                time.sleep(2 ** (attempt - 1))
        return None

    def put(self, key: str, data: bytes) -> None:
        name = self._name(key)
        self._retry("上传", lambda: self.client.put_object(Bucket=self.bucket, Key=name, Body=data))

    def get(self, key: str) -> bytes:
        name = self._name(key)

        def read() -> bytes:
            r = self.client.get_object(Bucket=self.bucket, Key=name)
            return r["Body"].get_raw_stream().read()

        return self._retry("读取", read)

    def exists(self, key: str) -> bool:
        try:
            name = self._name(key)
        except ValueError:
            return False
        return self._retry("查询", lambda: self.client.object_exists(Bucket=self.bucket, Key=name))

    def size(self, key: str) -> int:
        name = self._name(key)
        r = self._retry("查询", lambda: self.client.head_object(Bucket=self.bucket, Key=name))
        return int(r.get("Content-Length") or 0)


_store: ObjectStore | None = None


def get_store() -> ObjectStore:
    global _store
    if _store is None:
        s = get_settings()
        if s.storage_backend == "oss":
            _store = OssStore(s)
        elif s.storage_backend == "cos":
            _store = CosStore(s)
        else:
            _store = LocalStore(s.data_dir / "storage")
    return _store
