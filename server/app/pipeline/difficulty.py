"""难度评估基线（P1）。

按题型与题目在同类题中的位置估算预估得分率：同一题型中越靠后越难，这是国内试卷的普遍编排习惯。
P2 替换为大模型估计 + 历史作答数据校准。
"""

from .segment import Question

# 各题型第一题与最后一题的预估得分率
_RANGE = {"单选题": (0.88, 0.45), "多选题": (0.7, 0.35), "填空题": (0.8, 0.35), "解答题": (0.75, 0.25)}


def estimate(questions: list[Question]) -> list[float]:
    by_type: dict[str, list[int]] = {}
    for i, q in enumerate(questions):
        by_type.setdefault(q.type, []).append(i)
    coefs = [0.6] * len(questions)
    for qtype, idxs in by_type.items():
        hi, lo = _RANGE.get(qtype, (0.8, 0.35))
        n = len(idxs)
        for rank, i in enumerate(idxs):
            coefs[i] = round(hi - (hi - lo) * (rank / (n - 1) if n > 1 else 0.3), 2)
    return coefs
