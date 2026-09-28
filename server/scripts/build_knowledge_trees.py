"""把《小学·初中·高中基础学科知识点总纲》（Markdown）转换为内置知识树 JSON。

用法：
    uv run python scripts/build_knowledge_trees.py <知识点总纲.md> [--out 目录] [--only 高中数学,初中化学] [--no-llm] [--concurrency 4]

默认用大模型（server/.env 中的 LLM_*）把每个主题下的细目整理为规范的知识点名称，按主题编号合并；
未配置大模型、加 --no-llm 或某学科整理失败时退回规则拆分，个别遗漏的主题单独退回规则拆分。

思考型模型在细目较长的学科上可能因输出过长被截断（表现为大部分主题退回规则拆分），
可对该学科关闭思考重新生成，如通义千问：
    LLM_EXTRA_BODY='{"enable_thinking": false}' uv run python scripts/build_knowledge_trees.py <总纲.md> --only 高中政治

总纲结构 → 知识树层级：
    ## 第一部分　小学            → 学段
    ### 02　数学                 → 一棵知识树
    #### 数与式                  → 模块（一级）
    - **有理数和实数**：a、b；c   → 主题（二级）
      「、」「；」分隔的概念        → 细目（三级，叶子）；说明、提醒性质的句子不作为知识点
每个学科末尾的「关键关系」「易错与自检」等段落不纳入。
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

STAGE_RE = re.compile(r"^## 第.部分\s*(小学|初中|高中)")
SUBJECT_RE = re.compile(r"^### \d+\s*(.+)$")
MODULE_RE = re.compile(r"^#### (.+)$")
ITEM_RE = re.compile(r"^- \*\*(.+?)\*\*[：:]\s*(.+)$")
BOOK_RE = re.compile(r"^参考体系[：:]\s*([^，。；]+)")

# 与试卷分类（app/pipeline/classify.py）的学科名保持一致
SUBJECT_ALIASES = {"生物学": "生物", "思想政治": "政治"}

# 含这些字词的片段是说明或提醒，不作为知识点
NOTE_WORDS = re.compile(
    r"区分|不能|不可|不要|不是|不等于|不一定|不代表|未必|必须|须|需要|注意|避免|防止|检验|检查|核对|结合|理解|根据|依据|"
    r"明确|说明|判断|如何|怎样|为什么|能够|会|并|及其|时|的关系$|使用|运用|选择|兼顾|返回|分开|列明"
)
MAX_CONCEPT = 14


def concepts(detail: str) -> list[str]:
    """从「：」后的细目文本中取出名词性的概念。"""
    detail = re.sub(r"[（(][^）)]*[）)]", "", detail.rstrip("。 "))
    out: list[str] = []
    for seg in re.split(r"[；;]", detail):
        seg = seg.strip()
        # 含逗号的片段是完整句子（说明、规则），整段跳过
        if not seg or "，" in seg or "," in seg or NOTE_WORDS.search(seg):
            continue
        for c in re.split(r"[、]", seg):
            c = c.strip(" 　“”\"'")
            if 1 < len(c) <= MAX_CONCEPT and not re.search(r"[＝=＞＜≥≤→]", c) and c not in out:
                out.append(c)
    return out


LLM_SYSTEM = """你是中国中小学{stage}{subject}教研员。下面是知识点总纲中本学科的主题，每行以编号开头，后面是主题名和原文细目。
请把每个主题的细目整理为规范的知识点，只输出 JSON：
{{"topics": [{{"id": "t1", "kps": [{{"name": "知识点", "aliases": ["常见别称"]}}]}}]}}

要求：
1. 按编号输出每一个主题，id 与输入完全一致，一个都不能少。
2. 每个主题 2–10 个知识点，只能来自该主题的原文细目，不要新增原文没有的内容。
3. 知识点名称独立可读、符合教材说法，不超过 16 个字；把原文中共用后半句的写法补全，
   如「正弦、余弦、正切定义、图像」应整理为「正弦、余弦、正切的定义」「三角函数的图像与性质」这类完整名称。
4. 原文中提醒、易错、方法说明性质的句子（如「区分……」「不能……」「必须……」）不作为知识点。
5. aliases 为 0–2 个常见别称或简称（如「均值不等式」之于「基本不等式」），没有就留空数组。"""


def _topic_ids(tree: dict) -> list[tuple[str, int, int]]:
    """为每个主题编号 t1、t2…，大模型按编号返回，不依赖名称是否一致。"""
    ids, n = [], 0
    for mi, m in enumerate(tree["nodes"]):
        for ti, _ in enumerate(m["children"]):
            n += 1
            ids.append((f"t{n}", mi, ti))
    return ids


def _llm_input(tree: dict, raw: dict[str, str]) -> str:
    lines, ids = [], iter(_topic_ids(tree))
    for m in tree["nodes"]:
        lines.append(f"【模块】{m['name']}")
        for tp in m["children"]:
            tid = next(ids)[0]
            lines.append(f"  {tid}【{tp['name']}】{raw[m['name'] + '/' + tp['name']]}")
    return "\n".join(lines)


def _kps(t: dict) -> list[dict]:
    seen, out = set(), []
    for k in t.get("kps") or []:
        name = str(k.get("name") if isinstance(k, dict) else k).strip()[:20]
        if not name or name in seen or name == t.get("name"):
            continue
        seen.add(name)
        aliases = [str(a).strip() for a in (k.get("aliases") or [] if isinstance(k, dict) else []) if str(a).strip() and str(a).strip() != name][:2]
        out.append({"name": name, **({"aliases": aliases} if aliases else {})})
    return out[:10]


def _validate(tree: dict, data: object) -> tuple[list[dict], int]:
    """按主题编号合并大模型结果；遗漏或为空的主题保留规则拆分结果。返回 (节点, 退回规则拆分的主题数)。
    一个主题都对不上时抛出异常，整科退回规则拆分。"""
    topics = data.get("topics") if isinstance(data, dict) else None
    if not isinstance(topics, list):
        raise ValueError("缺少 topics")
    by_id = {str(t.get("id")).strip(): t for t in topics if isinstance(t, dict)}
    out = [{"name": m["name"], "children": list(m["children"])} for m in tree["nodes"]]
    matched = 0
    for tid, mi, ti in _topic_ids(tree):
        kps = _kps({**by_id[tid], "name": tree["nodes"][mi]["children"][ti]["name"]}) if tid in by_id else []
        if kps:
            matched += 1
            out[mi]["children"][ti] = {"name": tree["nodes"][mi]["children"][ti]["name"], "children": kps}
    if not matched:
        raise ValueError("没有可对应的主题")
    return out, len(_topic_ids(tree)) - matched


async def refine_with_llm(trees: list[dict], raw: dict[str, dict[str, str]], concurrency: int = 4, on_done=None) -> None:  # noqa: ANN001
    from app.config import get_settings
    from app.pipeline.llm import LLMError, chat_json, describe

    settings = get_settings()
    sem = asyncio.Semaphore(concurrency)

    async def one(t: dict) -> None:
        key = t["stage"] + t["subject"]
        async with sem:
            try:
                data = await chat_json(LLM_SYSTEM.format(stage=t["stage"], subject=t["subject"]),
                                       _llm_input(t, raw[key]), settings, purpose="kp_build", retries=2)
                t["nodes"], fallback = _validate(t, data)
                t["source"] = "llm"
                print(f"  ✓ {key}" + (f"（{fallback} 个主题使用规则拆分）" if fallback else ""), flush=True)
            except Exception as e:  # noqa: BLE001  单个学科失败不影响其他学科
                t["source"] = "rules"
                print(f"  ✗ {key}：{describe(e) if isinstance(e, LLMError) else f'{type(e).__name__}: {e}'}，使用规则拆分", flush=True)
            if on_done:
                on_done(t)

    await asyncio.gather(*(one(t) for t in trees))


def parse(md: str) -> tuple[list[dict], dict[str, dict[str, str]]]:
    """返回 (规则拆分的知识树, {学段学科: {模块/主题: 原文细目}})。"""
    raw: dict[str, dict[str, str]] = {}
    trees: list[dict] = []
    stage = None
    tree = module = None
    for line in md.splitlines():
        line = line.rstrip()
        if m := STAGE_RE.match(line):
            stage = m.group(1)
            tree = module = None
            continue
        if line.startswith("## "):  # 第四部分、参考资料等
            stage = tree = module = None
            continue
        if not stage:
            continue
        if m := SUBJECT_RE.match(line):
            subject_name = m.group(1).strip()
            subject = SUBJECT_ALIASES.get(subject_name, subject_name)
            tree = {"name": f"{stage}{subject_name}知识点总纲", "subject": subject, "stage": stage, "textbook": "", "nodes": []}
            trees.append(tree)
            module = None
            continue
        if tree is None:
            continue
        if m := BOOK_RE.match(line):
            tree["textbook"] = m.group(1).strip()
            continue
        if m := MODULE_RE.match(line):
            module = {"name": m.group(1).strip(), "children": []}
            tree["nodes"].append(module)
            continue
        if module is not None and (m := ITEM_RE.match(line)):
            topic, detail = m.group(1).strip(), m.group(2)
            raw.setdefault(tree["stage"] + tree["subject"], {})[module["name"] + "/" + topic] = detail.strip()
            node = {"name": topic}
            leaves = concepts(detail)
            if leaves:
                node["children"] = [{"name": c} for c in leaves]
            module["children"].append(node)
    return [t for t in trees if t["nodes"]], raw


def count(nodes: list[dict]) -> tuple[int, int]:
    total = leaves = 0
    for n in nodes:
        total += 1
        kids = n.get("children") or []
        if kids:
            t, lf = count(kids)
            total, leaves = total + t, leaves + lf
        else:
            leaves += 1
    return total, leaves


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "app" / "data" / "knowledge"))
    ap.add_argument("--only", default="", help="只生成指定学段学科，逗号分隔，如 高中数学,初中化学")
    ap.add_argument("--no-llm", action="store_true", help="不调用大模型，只用规则拆分")
    ap.add_argument("--concurrency", type=int, default=4, help="同时整理的学科数")
    args = ap.parse_args()

    trees, raw = parse(Path(args.source).read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        trees = [t for t in trees if t["stage"] + t["subject"] in wanted]
    for t in trees:
        t["source"] = "rules"

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not args.only:
        for old in out_dir.glob("*.json"):
            old.unlink()

    def write(t: dict) -> None:
        """每个学科完成后立即写出，中途出错也不会丢失已完成的结果。"""
        how = "大模型按原文细目整理知识点名称" if t["source"] == "llm" else "按原文细目规则拆分"
        t["note"] = f"由《小学·初中·高中基础学科知识点总纲》整理的内置知识树（{how}），按知识领域组织；学校有正式知识体系时请导入替换。"
        (out_dir / f"{t['stage']}{t['subject']}.json").write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")

    use_llm = False
    if not args.no_llm:
        from app.config import get_settings
        use_llm = get_settings().llm_enabled
        print(f"用大模型整理 {len(trees)} 个学科的知识点…" if use_llm else "未配置大模型，使用规则拆分", flush=True)
    if use_llm:
        asyncio.run(refine_with_llm(trees, raw, args.concurrency, on_done=write))
    else:
        for t in trees:
            write(t)
    for t in trees:
        total, leaves = count(t["nodes"])
        print(f"{t['stage']}{t['subject']:<6} {t['source']:<5} {t['textbook'][:18]:<20} 模块 {len(t['nodes']):>2}  节点 {total:>3}  知识点 {leaves:>3}")
    print(f"共 {len(trees)} 棵，输出到 {out_dir}")


if __name__ == "__main__":
    main()
