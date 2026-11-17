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


def extract_terms(workdir: Path, top: int = 80) -> list[dict]:
    """从 chunks 里统计高频英文词，供人工/agent 组术语表。

    只读 chunks.json，不碰 PDF——这正是「禁止自己写解析脚本」之外的合法助手。
    """
    import re as _re

    chunks = json.loads((workdir / CHUNKS_FILE).read_text(encoding="utf-8"))
    units = [u for c in chunks.get("chunks", []) for u in c.get("units", [])]
    total: dict[str, int] = {}
    pages: dict[str, set] = {}
    for unit in units:
        source = _re.sub(r"\$[^$]*\$", " ", str(unit.get("source", "")))
        source = _re.sub(r"\\[A-Za-z]+", " ", source)   # 去掉残留的 LaTeX 命令
        words = [w.lower() for w in _re.findall(r"[A-Za-z][A-Za-z\-]{2,}", source)]
        for word in set(words):
            if word in STOPWORDS:
                continue
            total[word] = total.get(word, 0) + words.count(word)
            pages.setdefault(word, set()).add(unit.get("page"))
    ranked = sorted(
        ((word, count) for word, count in total.items() if len(pages[word]) >= 2),
        key=lambda item: (-len(pages[item[0]]), -item[1], item[0]),
    )
    return [{"en": word, "count": count, "pages": len(pages[word])} for word, count in ranked[:top]]


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
        lines = ["en,count,pages"] + [f"{r['en']},{r['count']},{r['pages']}" for r in rows]
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
        paths.say(f"[术语] 候选 {len(rows)} 个已写入 {out}（按出现段数排序）")
    else:
        for row in rows:
            print(f"{row['en']}\t{row['count']}\t{row['pages']}")
    return paths.EXIT_OK


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "terms":
        sys.exit(main_terms(sys.argv))
    sys.exit(main(sys.argv))


