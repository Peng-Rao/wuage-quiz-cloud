"""学校别名合并与分校、附属学校的区分。"""
import pytest

from app.bank import REGULAR_SCHOOLS
from app.db import SessionLocal
from app.pipeline.classify import clean_school, rule_classify, rule_school

from .test_bank import MATH_META, _paper, client  # noqa: F401


@pytest.mark.parametrize("name, context, expected", [
    ("厦门一中", "", "厦门第一中学"),
    ("福建省厦门市第一中学", "", "厦门第一中学"),
    ("厦门十一中", "", "厦门第十一中学"),
    ("厦门同安一中", "", "厦门同安第一中学"),
    ("同安一中", "福建 · 厦门", "厦门同安第一中学"),
    ("槟榔中学", "福建省厦门市初三期中考试", "厦门槟榔中学"),
    ("双十中学", "福建 · 厦门", "厦门双十中学"),
    ("集美中学", "福建 · 厦门", "厦门集美中学"),
    ("松柏中学", "福建 · 厦门", "厦门松柏中学"),
    ("湖滨中学", "福建 · 厦门", "厦门湖滨中学"),
    ("湖里中学", "福建 · 厦门", "厦门湖里中学"),
    ("厦门厦门双十中学", "", "厦门双十中学"),
    ("厦门思明区厦门市莲花中学", "", "厦门莲花中学"),
    ("厦门明区厦门市莲花中学", "", "厦门莲花中学"),
    ("厦门市集美区厦门市集美区灌口中学", "", "厦门灌口中学"),
    ("厦门湖里五缘第二实验学校", "", "厦门五缘第二实验学校"),
    ("厦门一中集美分校", "", "厦门灌口中学"),
    ("厦门一中集美分校（灌口中学）", "", "厦门灌口中学"),
    ("厦门灌口中学（厦门一中集美分校）", "", "厦门灌口中学"),
    ("厦门第一中学（海沧校区）", "", "厦门第一中学海沧校区"),
    (" 福建省 厦门市 槟榔中学 ", "", "厦门槟榔中学"),
    ("集美中学", "北京", "集美中学"),
    ("诚毅中学", "福建 · 厦门", "厦门诚毅中学"),
    ("槟榔中学", "", "槟榔中学"),
    ("北京第一中学", "福建 · 厦门", "北京第一中学"),
    ("义务教育学校", "福建 · 厦门", ""),
    ("厦门市义务教育学校", "", ""),
])
def test_clean_school_aliases(name, context, expected):
    assert clean_school(name, context=context) == expected
    assert clean_school(expected, context=context) == expected


@pytest.mark.parametrize("title, expected", [
    ("福建福建省厦门双十中学思明分校2025学年期中考试", "厦门双十中学思明分校"),
    ("福建省厦门市海沧区厦门双十中学海沧附属学校2025学年期中考试", "厦门双十中学海沧附属学校"),
    ("厦门同安第一中学附属学校2024学年期末考试", "厦门同安第一中学附属学校"),
    ("厦门一中海沧校区2025学年期中考试", "厦门第一中学海沧校区"),
    ("厦门第一中学（思明校区）2025学年期中考试", "厦门第一中学思明校区"),
    ("厦门一中集美分校（灌口中学）2025学年期中考试", "厦门灌口中学"),
    ("厦门市义务教育学校2025学年期末考试", ""),
])
def test_rule_school_keeps_complete_name(title, expected):
    assert rule_school(title) == expected


@pytest.mark.parametrize("first, second", [
    ("厦门第一中学", "厦门同安第一中学"),
    ("厦门第一中学", "厦门灌口中学"),
    ("厦门第一中学思明校区", "厦门第一中学海沧校区"),
    ("厦门双十中学", "厦门双十中学思明分校"),
    ("厦门双十中学", "厦门双十中学海沧附属学校"),
    ("厦门同安第一中学", "厦门同安第一中学附属学校"),
    ("厦门实验中学", "厦门市湖里区实验中学"),
    ("厦门五缘实验学校", "厦门五缘第二实验学校"),
    ("厦门音乐学校", "厦门观音山音乐学校"),
])
def test_different_schools_stay_separate(first, second):
    assert clean_school(first) != clean_school(second)


def test_rule_classify_uses_filename_city_context():
    meta = rule_classify("槟榔中学九年级数学期中考试", "厦门市2025年数学试卷")
    assert meta.school == "厦门槟榔中学"


def test_paper_facets_merge_old_metadata_and_preserve_branches(client):
    prefix = "学校别名回归"
    samples = [
        ("厦门一中", "厦门第一中学", "厦门第一中学"),
        ("厦门第一中学", "厦门第一中学", "厦门第一中学"),
        ("槟榔中学", "厦门槟榔中学", "厦门槟榔中学"),
        ("厦门槟榔中学", "厦门槟榔中学", "厦门槟榔中学"),
        ("厦门厦门双十中学", "厦门双十中学", "厦门双十中学"),
        ("双十中学", "厦门双十中学", "厦门双十中学"),
        ("厦门双十中学", "厦门双十中学海沧附属学校", "厦门双十中学海沧附属学校"),
        ("厦门同安第一中学", "厦门同安第一中学附属学校", "厦门同安第一中学附属学校"),
        ("厦门第一中学", "厦门一中集美分校", "厦门灌口中学"),
        ("义务教育学校", "厦门市义务教育学校", "未分类"),
        ("厦门明区厦门市莲花中学", "厦门市思明区莲花中学", "厦门莲花中学"),
    ]
    ids = {}
    with SessionLocal() as s:
        for i, (school, title_school, expected) in enumerate(samples):
            pid = _paper(s, dict(MATH_META, title=f"{prefix}-{i}-{title_school}数学期中考试",
                                 school=school, region="福建 · 厦门"),
                         [{"type": "单选题", "score": 5, "stem": f"学校别名题{i}", "coef": .5}])
            ids.setdefault(expected, []).append(pid)
        s.commit()
    page = client.get("/api/papers", params={"q": prefix}).json()
    assert page["total"] == len(samples)
    # 学校筛选按高中常设学校归类，其余归入「其他」
    facet = {f["name"]: f["count"] for f in page["facets"]["schools"] if f["count"]}
    assert facet == {"厦门一中": 2, "厦门双十": 2, "其他": 6, "未分类": 1}
    for school, expected_ids in ids.items():
        filtered = client.get("/api/papers", params={"q": prefix, "school": school}).json()
        assert {p["id"] for p in filtered["items"]} == set(expected_ids)
    for alias, canonical in [("厦门一中", "厦门第一中学"), ("福建省厦门市槟榔中学", "厦门槟榔中学")]:
        filtered = client.get("/api/papers", params={"q": prefix, "school": alias}).json()
        assert {p["id"] for p in filtered["items"]} == set(ids[canonical])
    # 归一化仅影响展示和查询，历史元数据保留，便于继续人工核对。
    assert client.get(f"/api/papers/{ids['厦门槟榔中学'][0]}").json()["meta"]["school"] == "厦门槟榔中学"
    from app.db import ParseJob
    with SessionLocal() as s:
        assert s.get(ParseJob, ids["厦门槟榔中学"][0]).meta["school"] == "槟榔中学"


def test_filename_repairs_truncated_branch_but_keeps_manual_campus(client):
    from app.db import ParseJob
    prefix = "学校校区回归"
    with SessionLocal() as s:
        parent = _paper(s, dict(MATH_META, title=f"{prefix}-厦门双十中学", school="厦门双十中学"),
                        [{"type": "单选题", "score": 5, "stem": "附属学校试题", "coef": .5}])
        s.get(ParseJob, parent).file_name = "厦门双十中学海沧附属学校2025数学期中.pdf"
        campus = _paper(s, dict(MATH_META, title=f"{prefix}-厦门第一中学海沧校区", school="厦门第一中学思明校区"),
                        [{"type": "单选题", "score": 5, "stem": "校区试题", "coef": .5}])
        s.commit()
    assert client.get(f"/api/papers/{parent}").json()["meta"]["school"] == "厦门双十中学海沧附属学校"
    assert client.get(f"/api/papers/{campus}").json()["meta"]["school"] == "厦门第一中学思明校区"


def test_school_facet_lists_regular_schools_per_stage(client):
    prefix = "常设学校回归"
    samples = [
        ("初中", "厦门一中", "厦门一中"),
        ("初中", "槟榔中学", "槟榔中学"),
        ("初中", "厦门第二中学", "其他"),
        ("初中", "厦门外国语学校瑞景分校", "瑞景外国语中学"),
        ("高中", "厦门二中", "厦门二中"),
        ("高中", "厦门第一中学（海沧校区）", "厦门一中"),
        ("高中", "槟榔中学", "其他"),
        ("高中", "厦门双十中学海沧附属学校", "其他"),
        ("高中", "科技中学", "厦门科技中学"),
    ]
    ids: dict[tuple[str, str], list[str]] = {}
    with SessionLocal() as s:
        for i, (stage, school, bucket) in enumerate(samples):
            pid = _paper(s, dict(MATH_META, title=f"{prefix}-{i}", stage=stage, school=school, region="福建 · 厦门"),
                         [{"type": "单选题", "score": 5, "stem": f"常设学校题{i}", "coef": .5}])
            ids.setdefault((stage, bucket), []).append(pid)
        s.commit()
    for stage in ("初中", "高中"):
        page = client.get("/api/papers", params={"q": prefix, "stage": stage}).json()
        names = [f["name"] for f in page["facets"]["schools"]]
        # 常设学校按配置顺序全部列出（含 0 卷），「其他」在最后
        assert names[:-1] == list(REGULAR_SCHOOLS[stage]) and names[-1] == "其他"
        counts = {f["name"]: f["count"] for f in page["facets"]["schools"] if f["count"]}
        assert counts == {b: len(v) for (st, b), v in ids.items() if st == stage}
        for (st, bucket), expected in ids.items():
            if st == stage:
                got = client.get("/api/papers", params={"q": prefix, "stage": stage, "school": bucket}).json()
                assert {p["id"] for p in got["items"]} == set(expected)
    # 不选学段时，「厦门二中」只含高中试卷，初中的二中试卷在「其他」里
    got = client.get("/api/papers", params={"q": prefix, "school": "厦门二中"}).json()
    assert {p["id"] for p in got["items"]} == set(ids[("高中", "厦门二中")])
