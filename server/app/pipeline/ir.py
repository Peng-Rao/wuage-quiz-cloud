"""文档解析中间表示（IR）。所有解析引擎都输出 Block 列表，后续步骤只依赖 IR。"""

from dataclasses import dataclass, field
from typing import Literal

BlockType = Literal["text", "title", "equation", "image", "table"]


@dataclass
class Block:
    seq: int                                    # 全文阅读顺序
    page: int                                   # 从 1 开始
    bbox: tuple[float, float, float, float]     # 归一化 [x0, y0, x1, y1]，0–1
    type: BlockType
    content: str                                # 文本；公式为 LaTeX（$$...$$）；表格为 HTML
    image: bytes | None = None                  # 图片 / 表格截图原始字节
    image_ext: str = "png"
    score: float | None = None

    @property
    def text(self) -> str:
        return self.content.strip()


@dataclass
class ParsedDocument:
    blocks: list[Block]
    page_count: int
    # 用于渲染「查看原图」的 PDF 字节；docx 走云端且结果不含 PDF 时为 None
    pdf: bytes | None = None
    warnings: list[str] = field(default_factory=list)
