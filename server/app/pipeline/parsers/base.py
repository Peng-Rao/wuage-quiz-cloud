from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from ..ir import ParsedDocument

# 引擎内部进度 0–1，由流水线映射到 ocr 阶段的进度区间
ProgressFn = Callable[[float], Awaitable[None]]


@dataclass
class SourceFile:
    """待解析文件：已经过归一化，图片已合并为 PDF。"""

    name: str
    data: bytes
    kind: str  # pdf / docx


class ParserUnavailable(Exception):
    """引擎未配置或不支持该文件，路由应直接尝试下一个引擎（不计为失败）。"""


class ParserFailed(Exception):
    """引擎调用失败，路由记录告警后尝试下一个引擎。"""


class DocParser(Protocol):
    name: str

    async def parse(self, src: SourceFile, *, ocr: bool, on_progress: ProgressFn | None = None) -> ParsedDocument: ...
