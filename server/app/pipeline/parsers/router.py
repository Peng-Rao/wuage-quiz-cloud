"""按配置的引擎链依次尝试解析：未配置 / 不支持则跳过，失败则记录告警后降级。"""

import logging

from ...config import ParserName, Settings
from ..ir import ParsedDocument
from .base import DocParser, ParserFailed, ParserUnavailable, ProgressFn, SourceFile
from .lite import LiteParser
from .mineru_cloud import MinerUCloudParser

log = logging.getLogger(__name__)


class NotImplementedParser:
    """本地 MinerU（mineru-api）在 P3 接入。"""

    def __init__(self, name: str):
        self.name = name

    async def parse(self, src: SourceFile, *, ocr: bool, on_progress: ProgressFn | None = None) -> ParsedDocument:  # noqa: ARG002
        raise ParserUnavailable(f"{self.name} 尚未接入")


def build_parser(name: ParserName, settings: Settings) -> DocParser:
    if name == "mineru_cloud":
        return MinerUCloudParser(settings)
    if name == "lite":
        return LiteParser()
    return NotImplementedParser(name)


class AllParsersFailed(Exception):
    pass


async def parse_with_fallback(
    src: SourceFile, *, ocr: bool, settings: Settings, on_progress: ProgressFn | None = None,
    parsers: list[DocParser] | None = None,
) -> tuple[ParsedDocument, str, list[str]]:
    chain = parsers or [build_parser(n, settings) for n in settings.parser_chain]
    warnings: list[str] = []
    reasons: list[str] = []
    for p in chain:
        try:
            doc = await p.parse(src, ocr=ocr, on_progress=on_progress)
        except ParserUnavailable as e:
            reasons.append(f"{p.name}：{e}")
            continue
        except ParserFailed as e:
            log.warning("解析引擎 %s 失败：%s", p.name, e)
            reasons.append(f"{p.name}：{e}")
            warnings.append(f"{p.name} 解析失败，已降级：{e}")
            continue
        if not doc.blocks:
            reasons.append(f"{p.name}：未识别到内容")
            continue
        if reasons:
            warnings.append("已跳过：" + "；".join(reasons))
        return doc, p.name, warnings + doc.warnings
    raise AllParsersFailed("没有可用的解析引擎。" + "；".join(reasons))
