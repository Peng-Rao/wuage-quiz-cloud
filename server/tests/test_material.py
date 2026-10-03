"""阅读材料（英语阅读 / 完形填空原文、语文选文）的识别与挂接。样例取自真实试卷的版面单元。"""

from app.config import Settings
from app.pipeline.segment import Unit, find_materials, segment

PASSAGE_B = [
    "One day, Jack’s wife is cleaning out a closet (壁橱). “Look at all these umbrellas,” she says to Jack.",
    "“I can take them to the umbrella shop and let them repair all the umbrellas,” Jack says. "
    "“They are so good that we can’t throw them away.”",
    "Jack takes the eight umbrellas to the shop and leaves them there. “They will be OK tomorrow,” the shopkeeper says.",
]


def _units(*rows: str | tuple[str, str]) -> list[Unit]:
    return [Unit(f"u{i}", i, 1, *(r if isinstance(r, tuple) else ("text", r))) for i, r in enumerate(rows)]


async def _segment(us: list[Unit], with_answer: bool = True):  # noqa: ANN202
    return (await segment(us, Settings(), with_answer=with_answer)).questions


async def test_labeled_passages_attach_to_their_questions():
    """解析版：A 篇的答案解析之后是小标题 B 和 B 篇原文；原文不能拼进上一题的解析。"""
    us = _units(
        "Ⅲ.阅读理解（共两节，24小题，满分48分）",
        "第一节 阅读以下A、B两篇材料，从题中所给的A、B、C、D四个选项中，选出最佳答案。",
        "A",
        "Sunshine Primary School is looking for an English teacher. Our teachers have to be good with children. "
        "Pay: RMB 6,700 per month. Renji Hospital wants a nurse who can work on weekends.",
        "32. If Jane is good with children, she can work as ____.",
        "A. a teacher B. a nurse C. a waiter D. a doctor",
        "33. Which of these jobs makes the most money per month?",
        "A. A teacher. B. A nurse. C. A waiter. D. A driver.",
        "【答案】32. A 33. B",
        "【解析】",
        "【33题详解】",
        "细节理解题。根据“Pay: RMB 12,000 per month”可知，护士工资最高。故选B。",
        "B",
        *PASSAGE_B,
        "34. The underlined part “throw away” means ____.",
        "A. 丢弃 B. 赠送 C. 收藏 D. 使用",
        "35. ____ repairs the broken umbrellas.",
        "A. The shopkeeper B. Jack C. Jack’s wife D. The woman",
    )
    qs = await _segment(us)
    assert [q.printed_no for q in qs] == [32, 33, 34, 35]
    assert qs[0].material.startswith("Sunshine Primary School") and qs[1].material == qs[0].material
    assert qs[2].material == qs[3].material == "\n".join(PASSAGE_B)
    assert "umbrella" not in (qs[1].analysis or "") and qs[1].options[-1] == "A driver."
    assert qs[2].stem.startswith("The underlined part")


async def test_cloze_passage_after_heading():
    """完形填空：分节标题、说明之后的原文不能被当成大题说明丢掉；各空的题干为空，不再算作题干过短。"""
    us = _units(
        "第二节 完形填空",
        "从每小题所给的A、B、C三个选项中，选出可以填入空白处的最佳答案。（每小题1.5分，满分9分）",
        "Yang Weiyun is from Anhui and she’s 73 now. She is a good ____26____ and has taught Chinese for 30 years.",
        "But Yang ____27____ wanted to do something different. So she started to teach people by live streaming.",
        "26. A. teacher B. doctor C. worker",
        "27. A. even B. never C. really",
    )
    qs = await _segment(us, with_answer=False)
    assert [q.options for q in qs] == [["teacher", "doctor", "worker"], ["even", "never", "really"]]
    assert all(q.material and "____26____" in q.material for q in qs)
    assert all("题干过短" not in q.flags for q in qs)


async def test_unlabeled_passage_after_options_and_section_end():
    """没有小标题的英文段落紧接在上一题选项之后是新材料；分节标题之后的题不再挂上一篇材料。"""
    us = _units(
        "我们学校",
        "41. What is the best title?",
        "A. Lessons B. Schools C. Buildings D. Activities",
        *PASSAGE_B,
        "42. Who repairs the umbrellas?",
        "A. The shopkeeper B. Jack C. Jack’s wife D. The woman",
        "Ⅳ. 词汇运用（共10小题）",
        "43. My cat enjoys lying on my k____ on sunny afternoons.",
    )
    qs = await _segment(us, with_answer=False)
    assert qs[0].material is None and qs[0].options[-1] == "Activities"
    assert qs[1].material == "\n".join(PASSAGE_B)
    assert qs[2].material is None


async def test_labeled_figure_passage_with_short_text():
    """小标题 + 一两句说明 + 配图（家谱图）也是材料，配图归材料。"""
    us = _units(
        "B", "Hello, everyone. My name’s John. Here is the picture of my family tree. Let’s have a look.",
        ("image", "[图]"), "26. Bill is Eric’s ____ .", "A. father B. uncle C. cousin D. grandpa",
    )
    seg = await segment(us, Settings(), with_answer=False)
    q = seg.questions[0]
    assert q.material.startswith("Hello, everyone") and q.material_unit_ids == ["u1", "u2"]
    assert "u2" not in q.unit_ids


def test_not_materials():
    """不是材料：听力原文、排序题的 a. b. 条目、数学推导、题内的罗马数字条目、卷首说明、听力图片选项。"""
    cases = [
        _units("6. What are they mainly talking about?", "A. The doctor. B. Mario's cut. C. The weather.",
               "【答案】B 【解析】", "【原文】W: Does your cut hurt, Mario?",
               "It hurts a little, but the doctor has put some medicine on it and I feel much better now than "
               "I did yesterday morning when it happened at the park near our school.",
               "7. Where is Mario now?"),
        _units("45. Which is the correct order of the following things?",
               "a. We saw two people having a fight. b. Uncle Wang and I were walking in the park after dinner.",
               "c. Uncle Wang stopped the two men. d. I called the police and they came to the park very soon.",
               "A. b-d-c-a B. a-b-c-d C. d-b-a-c D. b-a-d-c", "46. What do you know about Uncle Wang?"),
        _units("18. 如图，在⊙O 中，弧 AC 等于弧 CB。", "【解析】", "【详解】证明：连接$OC$．", ("image", "[图]"),
               r"$\therefore \left\{\begin{matrix}a+2=b \\ a+b=4\end{matrix}\right.$，"
               r"$\therefore \left\{\begin{matrix}a=1 \\ b=3\end{matrix}\right.$，$\because EF/\!/MN$ and so on",
               "19. 计算：$\\sqrt{4}+1$"),
        _units("17. ZB是常用的阻燃剂。已知：", "I.用硼酸与ZnO、H$_{2}$O合成ZB，ZB的组成会受温度等合成条件的影响。",
               "Ⅱ.ZB受热，先释放出水；当温度高于350℃，生成ZnO和B$_{2}$O$_{3}$固体；继续升温到400℃以上，"
               "B$_{2}$O$_{3}$熔化为玻璃态物质，覆盖在可燃物表面隔绝空气，从而起到阻燃作用，这是 ZB 的主要用途。",
               "（1）ZB能起阻燃作用的原因是____(写一种)。", "18. 下列说法正确的是"),
        _units("初三年化学试卷", "（时间：60分钟，满分：100分）",
               "温馨提示：本学科试卷有两张，一是答题卡，另一是本试题（共8页，16题）；请将全部答案填在答题卡的相应答题栏内，"
               "否则不能得分。可能用到的相对原子质量：H 1 C 12 O 16 Na 23 Cl 35.5 Fe 56 Cu 64 Zn 65 Ag 108",
               "一、选择题", "1. 下列变化属于化学变化的是"),
        _units("1. 【此处可播放相关音频】", "A", ("image", "[图]"), "B", ("image", "[图]"), "C", ("image", "[图]"),
               "2. 【此处可播放相关音频】"),
    ]
    for us in cases:
        assert find_materials(us) == [], us[0].text


async def test_chinese_reading_passage():
    """语文：说明行之后的选文挂到后面各小题。"""
    passage = "我与父亲不相见已二年余了，我最不能忘记的是他的背影。那年冬天，祖母死了，父亲的差使也交卸了，正是祸不单行的日子。" * 4
    us = _units("二、现代文阅读（20分）", "阅读下面的文字，完成8～9题。", "背影", passage,
                "8. 文章题目是“背影”，请概括文中写了几次背影。", "9. 赏析画线句子的表达效果。")
    qs = await _segment(us, with_answer=False)
    assert [q.printed_no for q in qs] == [8, 9]
    assert all(q.material == f"背影\n{passage}" for q in qs)


async def test_numbering_restarts_are_questions_not_list_items():
    """分节后题号重新从 1 编起（选择题说明行之后、完形填空的各空）是题目，不能当作材料中的编号列表吞掉。"""
    long_line = "Mother’s Day was just one day away, and the boys were ready to give Mum a big ____1____. " * 2
    us = _units(
        "14. How many things will Susan help with on the farm?", "A. Two. B. Three. C. Four.",
        "15. What can Susan do after work?", "A. Ride a horse. B. Eat chicken. C. Play with the sheep.",
        "Ⅱ. 选择填空", "从A、B、C中选择一个最佳答案完成句子。（共2题，每题1分）",
        "1. —Look, there is ____ umbrella here.", "A. a B. the C. an",
        "2. My brother Tony ____ exercises.", "A. always B. often C. hardly ever",
        "二、语言知识运用（共一节，2小题）", "完形填空 从每小题所给的A、B、C三个选项中，选出可以填入空白处的最佳答案。",
        long_line, "“OK,” Max said, ____2____ the list. “Leo, you clean up the kitchen.”",
        "1. A. treasure B. surprise C. vacation", "2. A. looking at B. turning off C. hanging out",
    )
    qs = await _segment(us, with_answer=False)
    assert [q.printed_no for q in qs] == [14, 15, 1, 2, 1, 2]
    assert [q.material is not None for q in qs] == [False, False, False, False, True, True]
    assert qs[4].options == ["treasure", "surprise", "vacation"]


async def test_options_line_with_glued_heading_kept():
    """选项行后粘着下一部分的标题（「C. Yes, they are Ⅲ. 阅读理解。」）：选项保留在原题，后面的材料照常识别。"""
    us = _units(
        "10. —Are Lucy and Lily in the same school? —____ They are in Sunny High School.",
        "A. Yes, she is B. No, she isn’t C. Yes, they are Ⅲ. 阅读理解。(每小题 2 分，共 30 分)",
        "A", *PASSAGE_B, "11. Who repairs the umbrellas?", "A. The shopkeeper B. Jack C. Jack’s wife D. The woman",
    )
    qs = await _segment(us, with_answer=False)
    assert qs[0].options[:2] == ["Yes, she is", "No, she isn’t"] and qs[0].material is None
    assert qs[1].material == "\n".join(PASSAGE_B)
