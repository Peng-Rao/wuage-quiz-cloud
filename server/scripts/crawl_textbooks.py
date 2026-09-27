"""从国家中小学智慧教育平台抓取厦门市现用教材的目录，生成内置教材章节目录（app/data/chapters/*.json）。

用法：
    uv run python scripts/crawl_textbooks.py                       # 抓取并生成全部学科
    uv run python scripts/crawl_textbooks.py --only 高中数学,初中物理  # 只处理部分学科
    uv run python scripts/crawl_textbooks.py --no-llm              # 知识点对应只用名称匹配
    uv run python scripts/crawl_textbooks.py --offline             # 只用缓存，不访问网络

数据来源：平台「同步课堂」公开的教材列表与目录树（national_lesson/teachingmaterials、national_lesson/trees），
只抓取目录标题，不下载教材正文；请求之间有间隔，结果缓存在 data/cache/smartedu，重复运行不会重复请求。

版本依据：《厦门市小初高教材 PDF 检索与下载入口》（厦门市公开教材目录、福建省教材目录整理）。同一年级、册次有新旧两版时
取平台标注的「新教材」。个别学科在厦门存在多个版本（小学科学、高中化学），都生成，排在前面的为默认版本。

每节对应的知识点（knowledge）用于按章节筛选题目：
    1. 已有目录文件中同名章节的对应关系原样保留（可手工校订后重新运行）；
    2. 其余用大模型按本学科内置知识树选择（server/.env 中的 LLM_*，--no-thinking 关闭思考加快速度）；
    3. 未配置大模型或调用失败时，按名称相似度匹配知识树节点。
每项的 by 记录来源：manual 手工（没有 by 的视为手工）/ llm 大模型 / name 名称匹配；重新运行时保留 manual 与 llm，名称匹配重新计算。
"""

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import date
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.knowledge_tree import match_score  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CHAPTER_DIR = ROOT / "app" / "data" / "chapters"
KNOWLEDGE_DIR = ROOT / "app" / "data" / "knowledge"
LIST_URL = "https://s-file-1.ykt.cbern.com.cn/zxx/ndrs/national_lesson/teachingmaterials/version/data_version.json"
TREE_URL = "https://s-file-{n}.ykt.cbern.com.cn/zxx/ndrs/national_lesson/trees/{id}.json"
PLATFORM = "国家中小学智慧教育平台 · 同步课堂"

# 厦门市现用版本：(学段, 学科, 平台学科名, 平台版本名, 显示名称)；同一学科多个版本时第一个为默认
XIAMEN = [
    ("小学", "语文", "语文", "统编版", "统编版"),
    ("小学", "数学", "数学", "人教版", "人教版"),
    ("小学", "英语", "英语", "人教版（主编：吴欣）", "人教PEP版（三起）"),
    ("小学", "道德与法治", "道德与法治", "统编版", "统编版"),
    ("小学", "科学", "科学", "教科版", "教科版"),
    ("小学", "科学", "科学", "苏教版", "苏教版"),
    ("初中", "语文", "语文", "统编版", "统编版"),
    ("初中", "数学", "数学", "人教版", "人教版"),
    ("初中", "英语", "英语", "人教版", "人教版"),
    ("初中", "物理", "物理", "沪科技版", "沪科版"),
    ("初中", "化学", "化学", "人教版", "人教版"),
    ("初中", "生物", "生物学", "人教版", "人教版"),
    ("初中", "历史", "历史", "统编版", "统编版"),
    ("初中", "地理", "地理", "湘教版", "湘教版"),
    ("初中", "道德与法治", "道德与法治", "统编版", "统编版"),
    ("高中", "语文", "语文", "统编版", "统编版"),
    ("高中", "数学", "数学", "人教A版", "人教A版"),
    ("高中", "英语", "英语", "人教版", "人教版"),
    ("高中", "物理", "物理", "鲁科版", "鲁科版"),
    ("高中", "化学", "化学", "鲁科版", "鲁科版"),
    ("高中", "化学", "化学", "苏教版", "苏教版"),
    ("高中", "生物", "生物学", "人教版", "人教版"),
    ("高中", "历史", "历史", "统编版", "统编版"),
    ("高中", "地理", "地理", "人教版", "人教版"),
    ("高中", "政治", "思想政治", "统编版", "统编版"),
]

GRADES = ["一年级", "二年级", "三年级", "四年级", "五年级", "六年级", "七年级", "八年级", "九年级"]
# 新教材优先，其次未标注，最后旧教材
EDITION_RANK = {"新教材": 0, None: 1, "旧教材": 2}
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5}


def clean(title: str) -> str:
    """统一空白，去掉末尾的课时数「（5）」。"""
    t = re.sub(r"\s+", " ", title.replace("　", " ")).strip()
    return re.sub(r"\s*[（(]\d+[）)]$", "", t)


def tags(item: dict) -> dict[str, str]:
    return {t["tag_dimension_id"]: t["tag_name"] for t in item.get("tag_list") or []}


# ---------------- 抓取 ----------------

class Fetcher:
    def __init__(self, cache: Path, offline: bool, delay: float):
        self.cache, self.offline, self.delay = cache, offline, delay
        cache.mkdir(parents=True, exist_ok=True)
        self.client = httpx.Client(timeout=60, headers={"User-Agent": "fg-quiz-cloud textbook-catalog/1.0"})
        self.requests = 0

    def get(self, url: str, name: str) -> object:
        path = self.cache / name
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        if self.offline:
            raise FileNotFoundError(f"缓存中没有 {name}")
        last: Exception | None = None
        for attempt in range(3):
            time.sleep(self.delay * (attempt + 1))
            try:
                r = self.client.get(url)
                self.requests += 1
                r.raise_for_status()
                data = r.json()
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return data
            except Exception as e:  # noqa: BLE001
                last = e
        raise RuntimeError(f"请求失败 {url}：{last}")

    def materials(self) -> list[dict]:
        ver = self.get(LIST_URL, "teachingmaterials_version.json")
        urls = ver["urls"] if isinstance(ver["urls"], list) else ver["urls"].split(",")
        items: list[dict] = []
        for u in urls:
            items += self.get(u, "teachingmaterials_" + u.rsplit("/", 1)[-1])
        return items

    def tree(self, material_id: str) -> list[dict]:
        name = f"tree_{material_id}.json"
        for n in (1, 2, 3):
            try:
                return self.get(TREE_URL.format(n=n, id=material_id), name)
            except RuntimeError:
                if self.offline:
                    raise
        raise RuntimeError(f"目录树获取失败：{material_id}")


def pick_books(items: list[dict], stage: str, subject: str, version: str) -> list[list[dict]]:
    """该版本各年级、册次的候选教材（按年级册次排序），每组内新教材在前；平台上新教材目录尚未上线时退用旧教材。"""
    groups: dict[tuple[str, str], list[dict]] = {}
    for it in items:
        t = tags(it)
        if (t.get("zxxxd"), t.get("zxxxk"), t.get("zxxbb")) != (stage, subject, version):
            continue
        key = (t.get("zxxnj") or "", t.get("zxxcc") or "")
        rank = (EDITION_RANK.get(t.get("zxxxjjc"), 1), -int(it.get("update_time", "0")[:10].replace("-", "") or 0))
        groups.setdefault(key, []).append({**it, "_rank": rank, "_grade": key[0], "_volume": key[1], "_edition": t.get("zxxxjjc")})
    return [sorted(g, key=lambda b: b["_rank"]) for g in sorted(groups.values(), key=lambda g: book_order(g[0]))]


def book_order(b: dict) -> tuple:
    g, v = b["_grade"], b["_volume"]
    grade = GRADES.index(g) if g in GRADES else 99
    elective = 1 if "选择性" in v else 0
    num = next((CN_NUM[c] for c in re.findall(r"[一二三四五六1-5]", v)), 0)
    vol = {"上册": 0, "下册": 1, "全一册": 2}.get(v, 3)
    return (grade, elective, num, vol, v)


def book_name(stage: str, grade: str, volume: str) -> str:
    return f"{grade} {volume}" if stage != "高中" and grade else volume


def book_grade(stage: str, grade: str, volume: str) -> str:
    if grade:
        return grade
    if stage == "高中":
        return "高二" if "选择性" in volume else "高一"
    return ""


GENERIC_UNIT = re.compile(r"^第[一二三四五六七八九十\d]+单元$")
GENERIC_LESSON = re.compile(r"^第\s*\d+\s*课$")
CHAPTER_TITLE = re.compile(r"^第[一二三四五六七八九十\d]+章")
# 语文等学科单元下的分类：其下各课直接作为节
CATEGORY = re.compile(r"^(阅读|写作|名著导读|综合性学习|口语交际|课外古诗词诵读|古诗词诵读|单元研习任务|学习提示)$")


def kids(n: dict) -> list[dict]:
    return [c for c in n.get("child_nodes") or [] if clean(c["title"])]


def to_chapters(nodes: list[dict]) -> list[dict]:
    """把平台目录树整理为「章（单元）→ 节（课）」两级：
    - 「第一单元」下只有一个单元标题节点时，合并为「第一单元 标题」（初中历史）；
    - 单元下是「第X章」且各章有节时，以章为一级（初中生物：单元 → 章 → 节）；
    - 节为「第1课」且下面是课文时，节名为「第1课 课文／课文」（高中语文）；
    - 节为「阅读」「写作」等分类且下面有课文时，课文直接作为节（初中语文）；
    - 更深的课时、小标题不再细分。"""
    out: list[dict] = []
    for n in nodes:
        title, children = clean(n["title"]), kids(n)
        if GENERIC_UNIT.match(title) and len(children) == 1 and kids(children[0]):
            title, children = f"{title} {clean(children[0]['title'])}", kids(children[0])
        if children and any(CHAPTER_TITLE.match(clean(c["title"])) and kids(c) for c in children):
            # 各单元的章都从「第一章」编起，保留单元序号以免同名
            unit = m.group(1) + " " if (m := re.match(r"^(第[一二三四五六七八九十\d]+单元)", title)) else ""
            for c in children:
                out.append({"name": unit + clean(c["title"]), "sections": [{"name": clean(g["title"])} for g in kids(c)]})
            continue
        sections: list[dict] = []
        for c in children:
            ct, gs = clean(c["title"]), kids(c)
            if gs and GENERIC_LESSON.match(ct):
                sections.append({"name": f"{ct} " + "／".join(clean(g["title"]) for g in gs)})
            elif gs and CATEGORY.match(ct):
                sections += [{"name": clean(g["title"])} for g in gs]
            else:
                sections.append({"name": ct})
        out.append({"name": title, "sections": sections})
    return [c for c in out if c["name"]]


# ---------------- 知识点对应 ----------------

def tree_names(stage: str, subject: str) -> tuple[list[str], list[tuple[str, str]]] | None:
    """(全部节点名称, [(模块, 叶子)])"""
    f = KNOWLEDGE_DIR / f"{stage}{subject}.json"
    if not f.exists():
        return None
    data = json.loads(f.read_text(encoding="utf-8"))
    names: list[str] = []
    leaves: list[tuple[str, str]] = []

    for root in data["nodes"]:
        names.append(root["name"])
        for m in root.get("children") or []:
            names.append(m["name"])
            for leaf in m.get("children") or []:
                names.append(leaf["name"])
                leaves.append((m["name"], leaf["name"]))
    return names, leaves


class _Node:
    """match_score 需要的节点接口。"""

    def __init__(self, name: str):
        self.name, self.aliases = name, []


def lexical_map(section: str, chapter: str, names: list[str], limit: int = 3) -> list[str]:
    title = re.sub(r"^(第.+?[章单元节课]|[\dA-Za-z.]+|课题\s*\d+|Unit\s*\d+)\s*", "", section).strip() or section
    scored = sorted(((match_score(title, _Node(n)), n) for n in names), reverse=True)
    picked = [n for s, n in scored[:limit] if s >= 0.6]
    if not picked:  # 节名称匹配不到时用章名称
        ctitle = re.sub(r"^(第.+?[章单元]|[\d.]+)\s*", "", chapter).strip()
        scored = sorted(((match_score(ctitle, _Node(n)), n) for n in names), reverse=True)
        picked = [n for s, n in scored[:1] if s >= 0.6]
    return picked


SYSTEM = (
    "你是中小学教研员。给你一本教材的目录（章、节）和本学科的知识点清单，请为每一节选出它主要讲授的知识点。"
    "只能使用清单中出现的名称（可以选叶子知识点，也可以选模块名称表示整个模块），每节 0–6 个；复习、综合、活动类的节选择它覆盖的主要知识点；"
    "与知识点无关的（如阅读材料、口语交际）可以为空。只输出 JSON：{\"map\": {\"节编号\": [\"知识点\", ...]}}。"
)


async def llm_map(book: str, chapters: list[dict], leaves: list[tuple[str, str]], names: set[str], settings) -> dict[str, list[str]]:  # noqa: ANN001
    from app.pipeline.llm import chat_json

    by_module: dict[str, list[str]] = {}
    for m, leaf in leaves:
        by_module.setdefault(m, []).append(leaf)
    kn = "\n".join(f"【{m}】" + "、".join(ls) for m, ls in by_module.items())
    lines, index = [], {}
    for ci, c in enumerate(chapters):
        if not c["sections"]:  # 没有节的章（如实验活动）本身作为一项
            index[f"{ci + 1}"] = (ci, None)
            lines.append(f"[{ci + 1}] {c['name']}")
            continue
        lines.append(c["name"])
        for si, s in enumerate(c["sections"]):
            key = f"{ci + 1}.{si + 1}"
            index[key] = (ci, si)
            lines.append(f"  [{key}] {s['name']}")
    user = f"教材：{book}\n\n目录：\n" + "\n".join(lines) + f"\n\n知识点清单（【模块】叶子）：\n{kn}"
    data = await chat_json(SYSTEM, user, settings, purpose="chapter_map", retries=1)
    out: dict[str, list[str]] = {}
    for raw, vals in (data.get("map") or {}).items():
        key = str(raw).strip().strip("[]【】 ")  # 模型有时带上方括号
        if key in index and isinstance(vals, list):
            out[key] = [v for v in vals if isinstance(v, str) and v in names][:6]
    return out


def existing_mapping(path: Path) -> dict[tuple[str, str, str], tuple[list[str], str]]:
    """已有目录文件中（册, 章, 节）→ 知识点，用于保留手工校订。"""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    norm = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
    # 名称匹配的结果每次重新计算；手工校订（没有 by 或 by=manual）与大模型的结果保留
    kept = lambda x: x.get("by", "manual") in ("manual", "llm")  # noqa: E731
    out = {(norm(b["name"]), norm(c["name"]), norm(s["name"])): (s.get("knowledge") or [], s.get("by", "manual"))
           for b in data["books"] for c in b["chapters"] for s in c.get("sections", []) if kept(s)}
    out |= {(norm(b["name"]), norm(c["name"]), ""): (c.get("knowledge") or [], c.get("by", "manual"))
            for b in data["books"] for c in b["chapters"] if not c.get("sections") and "knowledge" in c and kept(c)}
    return out


# ---------------- 主流程 ----------------

async def build(args: argparse.Namespace) -> None:
    fetcher = Fetcher(Path(args.cache), args.offline, args.delay)
    items = fetcher.materials()
    only = {x.strip() for x in (args.only or "").split(",") if x.strip()}
    settings = get_settings()
    if args.no_thinking:
        settings.llm_extra_body = {**settings.llm_extra_body, "enable_thinking": False}
    use_llm = settings.llm_enabled and not args.no_llm
    sem = asyncio.Semaphore(args.concurrency)
    order: dict[tuple[str, str], int] = {}
    summary = []

    for stage, subject, p_subject, p_version, display in XIAMEN:
        if only and f"{stage}{subject}" not in only:
            continue
        order[(stage, subject)] = order.get((stage, subject), -1) + 1
        picked = pick_books(items, stage, p_subject, p_version)
        if not picked:
            print(f"!! {stage}{subject} {p_version}：平台上没有找到教材", file=sys.stderr)
            continue
        out_path = CHAPTER_DIR / f"{stage}{subject}-{display}.json"
        keep = existing_mapping(out_path)
        kt = tree_names(stage, subject)
        books = []
        for group in picked:
            # 新教材目录尚未上线（目录树为空）时退用旧教材
            b, nodes = group[0], []
            for cand in group:
                nodes = fetcher.tree(cand["id"])
                if nodes:
                    b = cand
                    break
            if not nodes:
                print(f"   {stage}{subject} {group[0]['_grade']}{group[0]['_volume']}：平台暂无目录，跳过", file=sys.stderr)
                continue
            name = book_name(stage, b["_grade"], b["_volume"])
            books.append({"name": name, "grade": book_grade(stage, b["_grade"], b["_volume"]),
                          "edition": b["_edition"] or "", "sourceId": b["id"], "chapters": to_chapters(nodes)})

        # 知识点对应：保留已有 → 大模型 → 名称匹配
        norm = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
        stats = {"kept": 0, "llm": 0, "lexical": 0, "empty": 0}

        async def map_book(book: dict) -> None:
            # 对应单位：有节的章按节，没有节的章按章本身（key 中节名为空）
            entries = [(f"{ci + 1}.{si + 1}", c, x, (norm(book["name"]), norm(c["name"]), norm(x["name"])))
                       for ci, c in enumerate(book["chapters"]) for si, x in enumerate(c["sections"])]
            entries += [(f"{ci + 1}", c, c, (norm(book["name"]), norm(c["name"]), ""))
                        for ci, c in enumerate(book["chapters"]) if not c["sections"]]
            for _, _, target, k in entries:
                if k in keep:
                    target["knowledge"], target["by"] = keep[k]
                    stats["kept"] += 1
            todo = [e for e in entries if "knowledge" not in e[2]]
            if not todo:
                return
            if kt is None:
                for _, _, target, _ in todo:
                    target["knowledge"], target["by"] = [], "name"
                    stats["empty"] += 1
                return
            names, leaves = kt
            got: dict[str, list[str]] | None = None
            if use_llm:
                try:
                    async with sem:
                        got = await llm_map(f"{stage}{subject} {display} {book['name']}", book["chapters"], leaves, set(names), settings)
                except Exception as e:  # noqa: BLE001
                    print(f"   大模型对应失败，改用名称匹配：{stage}{subject} {book['name']}：{e}", file=sys.stderr)
            for key, chapter, target, _ in todo:
                if got is not None and key in got:
                    target["knowledge"], target["by"] = got[key], "llm"
                    stats["llm"] += 1
                else:
                    target["knowledge"], target["by"] = lexical_map(target["name"], chapter["name"], names), "name"
                    stats["lexical"] += 1
                if not target["knowledge"]:
                    stats["empty"] += 1

        await asyncio.gather(*(map_book(b) for b in books))
        data = {
            "stage": stage, "subject": subject, "version": display, "order": order[(stage, subject)],
            "region": "厦门",
            "source": {"platform": PLATFORM, "version": p_version, "crawledAt": date.today().isoformat()},
            "note": "目录抓取自国家中小学智慧教育平台（同步课堂）；knowledge 为每节对应的知识点，由脚本生成，可手工校订后重新运行保留。",
            "books": books,
        }
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        n_sec = sum(len(c["sections"]) or 1 for b in books for c in b["chapters"])
        summary.append(f"{stage}{subject} {display}：{len(books)} 册，{sum(len(b['chapters']) for b in books)} 章，{n_sec} 节；"
                       f"知识点对应 保留 {stats['kept']} / 大模型 {stats['llm']} / 名称匹配 {stats['lexical']}，空 {stats['empty']}")
        print(summary[-1])
    print(f"完成：{len(summary)} 个版本，网络请求 {fetcher.requests} 次")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="只处理这些学段学科，如 高中数学,初中物理")
    ap.add_argument("--cache", default=str(ROOT / "data" / "cache" / "smartedu"))
    ap.add_argument("--offline", action="store_true", help="只用缓存")
    ap.add_argument("--delay", type=float, default=0.5, help="请求间隔（秒）")
    ap.add_argument("--no-llm", action="store_true", help="知识点对应只用名称匹配")
    ap.add_argument("--no-thinking", action="store_true", help="调用大模型时关闭思考（通义千问 enable_thinking=false）")
    ap.add_argument("--concurrency", type=int, default=6)
    asyncio.run(build(ap.parse_args()))


if __name__ == "__main__":
    main()
