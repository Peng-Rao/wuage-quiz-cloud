"""教材目录抓取脚本：目录树整理、版本选择（不访问网络）。"""

from scripts.crawl_textbooks import book_name, clean, lexical_map, pick_books, to_chapters


def n(title: str, *children: dict) -> dict:
    return {"title": title, "child_nodes": list(children)}


def test_clean_titles():
    assert clean("第十九章　二次根式") == "第十九章 二次根式"
    assert clean("单元一 结字规律（5）") == "单元一 结字规律"


def test_to_chapters_shapes():
    # 章 → 节 → 课时：课时不再细分
    math = to_chapters([n("第一章 空间向量", n("1.1 空间向量及其运算", n("1.1.1 线性运算")), n("1.2 基本定理"))])
    assert math == [{"name": "第一章 空间向量", "sections": [{"name": "1.1 空间向量及其运算"}, {"name": "1.2 基本定理"}]}]
    # 「第一单元」下只有单元标题：合并（初中历史）
    hist = to_chapters([n("第一单元", n("史前时期", n("第1课 远古人类"), n("第2课 原始农业")))])
    assert hist == [{"name": "第一单元 史前时期", "sections": [{"name": "第1课 远古人类"}, {"name": "第2课 原始农业"}]}]
    # 单元 → 章 → 节：以章为一级，保留单元序号（初中生物）
    bio = to_chapters([n("第一单元  生物和细胞", n("第一章 认识生物", n("第一节 生物的特征")), n("第二章 认识细胞", n("第一节 显微镜")))])
    assert [c["name"] for c in bio] == ["第一单元 第一章 认识生物", "第一单元 第二章 认识细胞"]
    # 「阅读」分类下的课文直接作为节；「第1课」下的课文合并为一节（语文）
    chn = to_chapters([n("第一单元", n("阅读", n("1 社戏"), n("2 回延安")), n("写作"), n("第3课", n("百合花"), n("哦，香雪")))])
    assert [x["name"] for x in chn[0]["sections"]] == ["1 社戏", "2 回延安", "写作", "第3课 百合花／哦，香雪"]
    # 没有节的章（实验活动）保留
    assert to_chapters([n("实验活动1 氧气的制取")]) == [{"name": "实验活动1 氧气的制取", "sections": []}]


def item(grade: str, volume: str, edition: str | None, updated: str, id_: str) -> dict:
    tags = [{"tag_dimension_id": "zxxxd", "tag_name": "初中"}, {"tag_dimension_id": "zxxxk", "tag_name": "数学"},
            {"tag_dimension_id": "zxxbb", "tag_name": "人教版"}, {"tag_dimension_id": "zxxnj", "tag_name": grade},
            {"tag_dimension_id": "zxxcc", "tag_name": volume}]
    if edition:
        tags.append({"tag_dimension_id": "zxxxjjc", "tag_name": edition})
    return {"id": id_, "update_time": updated, "tag_list": tags}


def test_pick_books_prefers_new_edition_and_orders_books():
    items = [
        item("八年级", "上册", "旧教材", "2024-03-15", "old8"),
        item("七年级", "下册", None, "2024-03-15", "7b"),
        item("七年级", "上册", "旧教材", "2024-03-15", "old7"),
        item("七年级", "上册", "新教材", "2024-12-24", "new7"),
        item("八年级", "上册", "新教材", "2025-07-29", "new8"),
        {**item("七年级", "上册", "新教材", "2024-12-24", "other"), "tag_list": []},  # 其他学科
    ]
    groups = pick_books(items, "初中", "数学", "人教版")
    assert [[b["id"] for b in g] for g in groups] == [["new7", "old7"], ["7b"], ["new8", "old8"]]
    assert book_name("初中", "七年级", "上册") == "七年级 上册" and book_name("高中", "", "必修 第一册") == "必修 第一册"


def test_lexical_map():
    names = ["子集", "交集与并集", "补集", "函数的单调性"]
    assert "函数的单调性" in lexical_map("3.2.1 函数的单调性", "第三章 函数", names)
