#!/usr/bin/env python3
"""middle_json → 翻译分块（chunks.json）。

只挑「会出现在最终 markdown 里、且应该翻译」的文字：
正文段落、各级标题、图注/表注、表格单元格。
行间公式、参考文献、页码/页脚/侧边水印、代码，全部跳过（提示词 4.3）。

用法：
    python seg.py --middle middle_json.json --out chunks.json [--glossary glossary.csv]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# 嵌入式 Python 的 ._pth 会关掉「脚本目录自动进 sys.path」，这里显式登记一次。
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
import middle
import paths

MAX_CHUNK_CHARS = 1200
CHUNKS_FILE = "chunks.json"
MAX_CHUNK_UNITS = 10


def build_chunks(units: list[middle.Unit]) -> list[dict]:
    """按字数/条数把单元打包成块，给 agent 一次读一块、译一块。"""
    chunks: list[dict] = []
    current: list[middle.Unit] = []
    current_chars = 0

    def flush() -> None:
        nonlocal current, current_chars
        if not current:
            return
        chunks.append(
            {
                "id": f"c{len(chunks) + 1:03d}",
                "kind": "mixed",
                "chars": current_chars,
                "units": [unit.to_dict() for unit in current],
            }
        )
        current = []
        current_chars = 0

    for unit in units:
        if current and (current_chars + unit.chars > MAX_CHUNK_CHARS
                        or len(current) >= MAX_CHUNK_UNITS):
            flush()
        current.append(unit)
        current_chars += unit.chars
    flush()
    return chunks


def read_glossary(path: Path | None) -> dict[str, str]:
    if not path or not path.is_file():
        return {}
    import csv

    terms: dict[str, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle):
            if len(row) >= 2 and row[0].strip() and not row[0].lstrip().startswith("#"):
                terms[row[0].strip()] = row[1].strip()
    return terms


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="把 MinerU 的 middle_json 切成可翻译的分块",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--middle", required=True, help="middle_json.json 路径")
    parser.add_argument("--out", required=True, help="输出的 chunks.json 路径")
    parser.add_argument("--glossary", help="术语表 glossary.csv（可选，只写入元信息）")
    args = parser.parse_args(argv[1:])

    paths.setup_stdout()
    middle_path = Path(args.middle).expanduser()
    if not middle_path.is_file():
        paths.fail(paths.EXIT_PARSE_FAILED, f"找不到 {middle_path}",
                   "解析没有成功，请重新跑一次解析。")

    try:
        mj = middle.load_middle(middle_path)
    except Exception as exc:  # pydantic 校验失败也要说人话
        paths.fail(paths.EXIT_PARSE_FAILED, f"middle_json 读不进来：{exc}",
                   "文件可能损坏或不是 MinerU 4.2 的产物，请重新解析。")

    units = list(middle.iter_units(mj))
    chunks = build_chunks(units)
    glossary = read_glossary(Path(args.glossary) if args.glossary else None)

    by_kind = Counter(unit.kind for unit in units)
    payload = {
        "schema": 1,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "middle_json": middle_path.name,
        "middle_sha256": middle.sha256_text(middle_path.read_text(encoding="utf-8")),
        "glossary_sha256": middle.sha256_text(
            "\n".join(f"{k},{v}" for k, v in sorted(glossary.items()))
        ),
        "glossary_terms": len(glossary),
        "stats": {
            "units": len(units),
            "chars": sum(unit.chars for unit in units),
            "chunks": len(chunks),
            "by_kind": dict(by_kind),
        },
        "chunks": chunks,
    }
    out_path = Path(args.out).expanduser()
    paths.ensure_dir(out_path.parent)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    avg = payload["stats"]["chars"] // max(len(units), 1)
    paths.say(f"[分块] 共 {len(units)} 段 / {payload['stats']['chars']} 字（平均 {avg} 字/段）")
    paths.say(f"[分块] 内部打包 {len(chunks)} 批仅供参考；next --limit 按「段」取，与批数无关：{out_path}")
    paths.say(f"[分块] 类型分布：{dict(by_kind)}")
    return paths.EXIT_OK


STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "are", "was", "were", "from", "which",
    "have", "has", "had", "not", "but", "its", "also", "such", "been", "their", "these",
    "than", "then", "when", "where", "while", "into", "over", "under", "between", "within",
    "can", "may", "using", "used", "use", "based", "shown", "figure", "table", "fig",
    "eq", "equation", "section", "however", "therefore", "thus", "more", "most", "some",
    "each", "both", "other", "only", "very", "will", "would", "could", "should", "does",
    "did", "one", "two", "three", "all", "any", "how", "who", "what", "about", "above",
    "after", "before", "due", "since", "upon", "via", "per", "well", "also", "been",
    "being", "between", "during", "through", "without", "these", "those", "such",
}


def _singular(word: str) -> str:
    """极简词形归并：只处理常见复数，够术语表用。"""
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "sses", "xes", "zes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


PHRASE_STOP = STOPWORDS | {
    "the", "of", "and", "to", "in", "for", "with", "by", "on", "at", "as", "from",
    "that", "this", "these", "those", "is", "are", "was", "were", "be", "been",
    "being", "it", "its", "we", "our", "they", "their", "which", "who", "whom",
    "whose", "or", "but", "not", "no", "if", "then", "than", "so", "such", "can",
    "could", "may", "might", "will", "would", "should", "do", "does", "did",
    "have", "has", "had", "also", "more", "most", "some", "any", "each", "both",
    "other", "only", "very", "all", "one", "two", "three", "first", "second",
    "new", "used", "use", "using", "based", "show", "shows", "shown", "study",
    "paper", "result", "results", "method", "methods", "approach", "proposed",
    "present", "figure", "fig", "table", "tab", "section", "eq", "equation",
    "respectively", "however", "therefore", "thus", "obtained", "increase",
    "decrease", "effect", "case", "time", "due", "after", "before", "during",
    "between", "within", "under", "over", "about", "into", "through",
}
GENERIC_WORDS = {
    "complete", "corresponding", "upper", "lower", "main", "four", "range",
    "ranges", "whereas", "conditions", "condition", "calculations", "calculation",
    "various", "different", "similar", "large", "small", "high", "low", "good",
    "strong", "weak", "significant", "slightly", "approximately", "nearly",
    "shown", "given", "following", "previous", "next", "total", "overall",
}
WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-]{2,}")


def extract_terms(workdir: Path, top: int = 80) -> list[dict]:
    """从 chunks 统计候选术语：短语优先、词形归并、过滤泛词。

    只读 chunks.json，不碰 PDF——这是「禁止自己写解析脚本」之外的合法助手。
    输出带 kind（phrase / word），建术语表时优先选 phrase。
    """
    chunks = json.loads((workdir / CHUNKS_FILE).read_text(encoding="utf-8"))
    units = [u for c in chunks.get("chunks", []) for u in c.get("units", [])]

    stats: dict[str, dict] = {}

    def add(term: str, kind: str, count: int, page) -> None:
        entry = stats.setdefault(term, {"count": 0, "pages": set(), "kind": kind})
        entry["count"] += count
        entry["pages"].add(page)
        if kind == "phrase":
            entry["kind"] = "phrase"

    for unit in units:
        page = unit.get("page")
        source = re.sub(r"\$[^$]*\$", " ", str(unit.get("source", "")))
        source = re.sub(r"\\[A-Za-z]+", " ", source)
        tokens = [w.lower() for w in WORD_RE.findall(source)]
        for word in set(tokens):
            if word in STOPWORDS or word in GENERIC_WORDS:
                continue
            add(_singular(word), "word", tokens.count(word), page)
        for size in (2, 3):
            seen: set[str] = set()
            for index in range(len(tokens) - size + 1):
                seq = tokens[index:index + size]
                if any(token in PHRASE_STOP for token in seq):
                    continue
                if len(set(seq)) < 2:
                    continue
                phrase = " ".join(seq)
                if phrase in seen:
                    continue
                seen.add(phrase)
                add(phrase, "phrase", 1, page)

    ranked = [
        (term, entry) for term, entry in stats.items()
        if len(entry["pages"]) >= 2 and entry["count"] >= 2
    ]
    # 短语略加权：同段数时短语优先，段数差 1 以内也优先给短语
    ranked.sort(key=lambda item: (
        -(len(item[1]["pages"]) + (2 if item[1]["kind"] == "phrase" else 0)),
        0 if item[1]["kind"] == "phrase" else 1,
        -item[1]["count"],
        item[0],
    ))
    return [
        {"en": term, "count": entry["count"], "pages": len(entry["pages"]),
         "kind": entry["kind"]}
        for term, entry in ranked[:top]
    ]


def main_terms(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="从 chunks.json 提取候选术语（只读）")
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--top", type=int, default=80)
    parser.add_argument("--out", help="写成 CSV（en,count,pages），UTF-8")
    args = parser.parse_args(argv[1:])
    workdir = Path(args.workdir).expanduser().resolve()
    if not (workdir / CHUNKS_FILE).is_file():
        paths.fail(paths.EXIT_PARSE_FAILED, f"找不到 {workdir / CHUNKS_FILE}",
                   "先跑 prepare 生成分块。")
    rows = extract_terms(workdir, args.top)
    if args.out:
        out = Path(args.out).expanduser()
        lines = ["en,count,pages,kind"] + [
            f"{r['en']},{r['count']},{r['pages']},{r['kind']}" for r in rows
        ]
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        paths.say(f"[术语] 候选 {len(rows)} 个已写入 {out}（按出现段数排序）")
    else:
        for row in rows:
            print(f"{row['en']}\t{row['count']}\t{row['pages']}\t{row['kind']}")
    return paths.EXIT_OK


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "terms":
        sys.exit(main_terms(sys.argv))
    sys.exit(main(sys.argv))


