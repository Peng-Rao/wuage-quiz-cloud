"""难度评估基线（P1）。

难度系数 0–1，越高越难，1 为最难（约等于 1 − 预估得分率）。
按题型与题目在同类题中的位置估算：同一题型中越靠后越难，这是国内试卷的普遍编排习惯。
P2 替换为大模型估计 + 历史作答数据校准。
"""

from .segment import Question

# 各题型第一题与最后一题的难度系数
_RANGE = {"单选题": (0.12, 0.55), "多选题": (0.3, 0.65), "填空题": (0.2, 0.65), "解答题": (0.25, 0.75)}


def estimate(questions: list[Question]) -> list[float]:
    by_type: dict[str, list[int]] = {}
    for i, q in enumerate(questions):
        by_type.setdefault(q.type, []).append(i)
    coefs = [0.4] * len(questions)
    for qtype, idxs in by_type.items():
        lo, hi = _RANGE.get(qtype, (0.2, 0.65))
        n = len(idxs)
        for rank, i in enumerate(idxs):
            coefs[i] = round(lo + (hi - lo) * (rank / (n - 1) if n > 1 else 0.3), 2)
    return coefs
