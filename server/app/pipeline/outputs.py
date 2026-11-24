"""大模型输出的数据结构（Pydantic）。

字段宽松：多余字段忽略，类型能转换就转换（"3" → 3），单个字段无法使用时取默认值而不是让整份输出作废；
只有整体结构不对（如缺少题目列表）才算不合格，由 llm.structured 要求模型重答。与输入相关的检查（题号、单元编号等）
由各调用方的 check 完成。
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


def _str_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (str, int, float)):
        return [str(v)]
    return [str(x) for x in v if isinstance(x, (str, int, float))] if isinstance(v, list) else []


class QuestionItem(Lenient):
    """知识点标注、难度评估、候选选择共用的单题结果。"""

    no: int | None = None
    kps: list[str] = []
    ids: list[str] = []
    other: list[str] = []
    difficulty: float | None = None

    @field_validator("no", mode="before")
    @classmethod
    def _no(cls, v: Any) -> int | None:
        try:
            return int(str(v).strip().lstrip("第").rstrip("题").strip())
        except (TypeError, ValueError):
            return None

    @field_validator("kps", "ids", "other", mode="before")
    @classmethod
    def _lists(cls, v: Any) -> list[str]:
        return _str_list(v)

    @field_validator("difficulty", mode="before")
    @classmethod
    def _difficulty(cls, v: Any) -> float | None:
        try:
            d = float(v)
        except (TypeError, ValueError):
            return None
        return round(d, 2) if 0 <= d <= 1 else None


def _wrap(key: str, v: Any) -> Any:
    """要求的格式是 {key: [...]}，部分模型（如 qwen3.8）会省略外层对象、直接返回数组，两种都接受。"""
    return {key: v} if isinstance(v, list) else v


class QuestionItems(Lenient):
    questions: list[QuestionItem]

    @model_validator(mode="before")
    @classmethod
    def _accept_list(cls, v: Any) -> Any:
        v = _wrap("questions", v)
        if isinstance(v, dict) and isinstance(v.get("questions"), list):
            v = {**v, "questions": [x for x in v["questions"] if isinstance(x, dict)]}
        return v


class SegmentOutput(Lenient):
    """拆题分组：题目内容交给 segment._validate_llm 逐项校验与修正，这里只要求有题目列表。"""

    questions: list[Any]

    @model_validator(mode="before")
    @classmethod
    def _accept_list(cls, v: Any) -> Any:
        return _wrap("questions", v)


class AnswerOutput(Lenient):
    answer: str = ""
    analysis: str | None = None
    uncertain: bool = False
    reason: str | None = None

    @field_validator("answer", mode="before")
    @classmethod
    def _answer(cls, v: Any) -> str:
        if isinstance(v, list):
            return "；".join(str(x) for x in v)
        return "" if v is None else str(v)

    @field_validator("analysis", "reason", mode="before")
    @classmethod
    def _text(cls, v: Any) -> str | None:
        return None if v is None else str(v)

    @field_validator("uncertain", mode="before")
    @classmethod
    def _bool(cls, v: Any) -> bool:
        if isinstance(v, str):
            return v.strip().lower() in ("true", "yes", "1", "是")
        return bool(v)

