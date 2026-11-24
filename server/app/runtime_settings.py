"""在管理后台修改、运行时生效的配置（API Key、模型等）。

配置保存在 app_setting 表中，覆盖环境变量；删除即恢复环境变量的值。每个进程（网页服务、Worker）按
SYNC_INTERVAL 秒检查 app_meta 中的版本号，变化时把覆盖值写入 get_settings() 返回的同一个 Settings 对象，
因此读取 get_settings().llm_api_key 等的代码无需改动，修改后几秒内所有进程生效，不必重启。
"""

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import delete, select

from .config import Settings, get_settings
from .db import AppMeta, AppSetting, SessionLocal, utcnow

log = logging.getLogger(__name__)

VERSION_KEY = "settings_version"
SYNC_INTERVAL = 5.0


@dataclass(frozen=True)
class Editable:
    key: str
    group: str
    label: str
    kind: str = "text"  # text / secret / select / json / number
    help: str = ""
    options: tuple[str, ...] = ()


EDITABLE: tuple[Editable, ...] = (
    Editable("llm_base_url", "大模型", "接口地址", help="OpenAI 兼容地址，如 https://api.deepseek.com/v1"),
    Editable("llm_api_key", "大模型", "API Key", "secret"),
    Editable("llm_model", "大模型", "模型", help="如 deepseek-chat、qwen-plus；地址、Key、模型都填写后才启用"),
    Editable("llm_extra_body", "大模型", "附加请求参数", "json", help='JSON 对象，如 {"enable_thinking": false}'),
    Editable("vision_base_url", "看图模型", "接口地址", help="留空沿用大模型地址"),
    Editable("vision_api_key", "看图模型", "API Key", "secret", help="留空沿用大模型 Key"),
    Editable("vision_model", "看图模型", "模型", help="留空则大模型看不到题目配图"),
    Editable("embedding_base_url", "向量检索", "接口地址", help="留空沿用大模型地址"),
    Editable("embedding_api_key", "向量检索", "API Key", "secret", help="留空沿用大模型 Key"),
    Editable("embedding_model", "向量检索", "模型", help="更换模型后需重新生成向量（embedding_backfill）"),
    Editable("mineru_base_url", "MinerU 云端解析", "接口地址"),
    Editable("mineru_token", "MinerU 云端解析", "Token", "secret", help="https://mineru.net/apiManage 创建"),
    Editable("mineru_model_version", "MinerU 云端解析", "模型版本", "select", options=("vlm", "pipeline")),
    Editable("price_billing_source", "成本计价", "计费来源", "select",
             help="auto 按大模型地址判断调用平台", options=("auto", "tencent", "aliyun", "openrouter")),
    Editable("price_refresh_hours", "成本计价", "单价抓取间隔（小时）", "number", help="0 为不自动抓取"),
)
BY_KEY = {e.key: e for e in EDITABLE}

_lock = threading.Lock()
_state: dict[str, Any] = {"version": None, "checked": 0.0, "applied": set()}


@lru_cache
def env_settings() -> Settings:
    """仅由环境变量（与 server/.env）得到的配置，用于恢复被删除的覆盖值、显示来源。"""
    return Settings()


def validate(key: str, value: Any) -> Any:
    spec = BY_KEY.get(key)
    if spec is None:
        raise ValueError(f"不支持修改 {key}")
    if spec.kind == "json" and isinstance(value, str):
        try:
            value = json.loads(value) if value.strip() else {}
        except json.JSONDecodeError as e:
            raise ValueError(f"{spec.label}不是有效的 JSON") from e
    if isinstance(value, str):
        value = value.strip()
    if spec.options and value not in spec.options:
        raise ValueError(f"{spec.label}只能是 {' / '.join(spec.options)}")
    try:
        value = TypeAdapter(Settings.model_fields[key].annotation).validate_python(value)
    except ValidationError as e:
        raise ValueError(f"{spec.label}格式不正确") from e
    if spec.kind == "number" and value < 0:
        raise ValueError(f"{spec.label}不能为负数")
    if key.endswith("base_url") and value and not str(value).startswith(("http://", "https://")):
        raise ValueError(f"{spec.label}须以 http:// 或 https:// 开头")
    return value


def _apply(rows: dict[str, str]) -> None:
    settings, env = get_settings(), env_settings()
    applied: set[str] = _state["applied"]
    for key in BY_KEY:
        if key in rows:
            try:
                setattr(settings, key, validate(key, json.loads(rows[key])))
                applied.add(key)
            except (ValueError, json.JSONDecodeError):
                log.warning("忽略无效的后台配置 %s", key)
        elif key in applied:
            # 覆盖被删除：恢复环境变量的值。从未覆盖过的键不动（测试中的 monkeypatch 等）
            setattr(settings, key, getattr(env, key))
            applied.discard(key)


def sync(force: bool = False) -> None:
    """配置版本变化时重新加载覆盖值；未到检查间隔时直接返回，开销可忽略。数据库不可用时保持当前配置。"""
    now = time.monotonic()
    if not force and now - _state["checked"] < SYNC_INTERVAL:
        return
    with _lock:
        if not force and now - _state["checked"] < SYNC_INTERVAL:
            return
        _state["checked"] = now
        try:
            with SessionLocal() as s:
                version = s.scalar(select(AppMeta.value).where(AppMeta.key == VERSION_KEY))
                if not force and version == _state["version"]:
                    return
                rows = {r.key: r.value for r in s.scalars(select(AppSetting))}
        except Exception as e:  # noqa: BLE001
            log.warning("读取后台配置失败：%s", e)
            return
        _apply(rows)
        _state["version"] = version


def save(changes: dict[str, Any], actor: str) -> list[str]:
    """保存修改：值为 None 时删除覆盖（恢复环境变量）。校验全部通过后才写入。返回实际变化的键。"""
    clean = {k: (None if v is None else validate(k, v)) for k, v in changes.items()}
    changed: list[str] = []
    with SessionLocal() as s:
        current = {r.key: r for r in s.scalars(select(AppSetting).where(AppSetting.key.in_(list(clean))))}
        for key, value in clean.items():
            row = current.get(key)
            if value is None:
                if row is not None:
                    s.execute(delete(AppSetting).where(AppSetting.key == key))
                    changed.append(key)
                continue
            text = json.dumps(value, ensure_ascii=False)
            if row is None:
                s.add(AppSetting(key=key, value=text, updated_by=actor, updated_at=utcnow()))
            elif row.value != text:
                row.value, row.updated_by, row.updated_at = text, actor, utcnow()
            else:
                continue
            changed.append(key)
        if changed:
            meta = s.get(AppMeta, VERSION_KEY)
            if meta is None:
                s.add(AppMeta(key=VERSION_KEY, value=uuid.uuid4().hex))
            else:
                meta.value = uuid.uuid4().hex
        s.commit()
    if changed:
        sync(force=True)
    return changed


def mask(value: str) -> str:
    if not value:
        return ""
    return value[:3] + "****" + value[-4:] if len(value) >= 12 else "****"


def describe() -> list[dict[str, Any]]:
    """各配置项的当前值（密钥只返回掩码）与来源：console 后台修改 / env 环境变量 / default 默认值。"""
    sync(force=True)
    settings, env = get_settings(), env_settings()
    with SessionLocal() as s:
        rows = {r.key: r for r in s.scalars(select(AppSetting))}
    out = []
    for spec in EDITABLE:
        value = getattr(settings, spec.key)
        row = rows.get(spec.key)
        if row is not None:
            source = "console"
        elif getattr(env, spec.key) != Settings.model_fields[spec.key].default:
            source = "env"
        else:
            source = "default"
        secret = spec.kind == "secret"
        out.append({
            "key": spec.key, "group": spec.group, "label": spec.label, "kind": spec.kind, "help": spec.help,
            "options": list(spec.options), "value": mask(value) if secret else value,
            "configured": bool(value), "source": source,
            "updatedBy": row.updated_by if row else None, "updatedAt": row.updated_at if row else None,
        })
    return out
