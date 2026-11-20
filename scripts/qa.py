#!/usr/bin/env python3
"""三道闸门 + 引用 + 语言检查，结果写 qa.json（插件式判定，不靠模型自觉）。

闸门（提示词 4.4）：
  公式：$$ 对数、行内 $ 数、每块 LaTeX 与原文完全一致
  图片：![](images/...) 路径集合与原文完全一致
  表格：标签骨架、单元格数量、HTML/管道表数量与原文一致
  引用：[12] / Fig. 3 / Table 2 / Eq. 5 出现次数一致
  语言：残留英文占比 < 5%

任何一项不过都写进 qa.json，并让脚本以退出码 6 结束，不许静默通过。

用法：
    python qa.py --workdir <产物目录> [--json]
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

IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
IMG_TAG_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.I)
DOLLAR_BLOCK_RE = re.compile(r"\$\$(.+?)\$\$", re.S)
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-]{1,}")
URL_RE = re.compile(r"https?://\S+|10\.\d{4,}/\S+|\b[\w.+-]+@[\w-]+\.[\w.]+\b")
# 引用计数必须能处理复数与省略写法：`Figs. 5 and 6`、`(Figure 1 and 2)`、`图 1、图 2`。
_NUM = r"\d{1,3}"
_NUM_LIST = rf"{_NUM}(?:\s*(?:,|and|&|to|–|-|~|、|和|与|及)\s*{_NUM})*"
_NUM_RE = re.compile(r"\d{1,3}")

CITE_PATTERNS = {
    "bracket": re.compile(rf"\[({_NUM_LIST})\]"),
    "fig": re.compile(rf"\bFigs?\.?|\bFigures?\.?|\bFig\.?\s*", re.I),
    "fig_zh": re.compile(rf"图\s*[(（]?\s*({_NUM_LIST})\s*[)）]?"),
    "table": re.compile(rf"\bTables?\.?\s*({_NUM_LIST})", re.I),
    "table_zh": re.compile(rf"表\s*[(（]?\s*({_NUM_LIST})\s*[)）]?"),
    "eq": re.compile(rf"\bEqs?\.?|\bEquations?\.?\s*({_NUM_LIST})", re.I),
    "eq_zh": re.compile(rf"式\s*[(（]?\s*({_NUM_LIST})\s*[)）]?"),
}
CITE_GROUPED = {
    "bracket": re.compile(rf"\[({_NUM_LIST})\]"),
    "fig": re.compile(rf"\bFigs?\.?\s*({_NUM_LIST})|\bFigures?\s*({_NUM_LIST})", re.I),
    "fig_zh": re.compile(rf"图\s*[(（]?\s*({_NUM_LIST})\s*[)）]?"),
    "table": re.compile(rf"\bTables?\s*({_NUM_LIST})", re.I),
    "table_zh": re.compile(rf"表\s*[(（]?\s*({_NUM_LIST})\s*[)）]?"),
    "eq": re.compile(rf"\bEqs?\.?\s*\(?({_NUM_LIST})|\bEquations?\s*\(?({_NUM_LIST})", re.I),
    "eq_zh": re.compile(rf"式\s*[(（]?\s*({_NUM_LIST})\s*[)）]?"),
}

# 判断「英文残留」时只看小写开头的英文词：专有名词（Transformer/BLEU/作者名）不算残留
PROSE_WORD_RE = re.compile(r"\b[a-z][a-z\-]{2,}\b")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def image_paths(md: str) -> set[str]:
    """同时认 Markdown 语法与 <img> 标签（默认渲染用的是后者）。"""
    return {match.strip() for match in IMG_RE.findall(md)} | {
        match.strip() for match in IMG_TAG_RE.findall(md)
    }


def formula_stats(md: str) -> dict[str, int]:
    blocks = DOLLAR_BLOCK_RE.findall(md)
    inline = len(re.findall(r"(?<!\$)\$(?!\$)", md))
    return {"display_blocks": len(blocks), "inline_dollars": inline}


def lenient_latex(text: str) -> str:
    """与 translate.validate_unit 完全一致的口径：去空白 + 去末尾标点。

    两处必须同口径：否则 apply 放行的译文会在 finish 被判失败，
    用户要重跑三轮才发现只是 `$D_{i},$` 与 `$D_{i}$` 的差别。
    """
    return re.sub(r"[).,;:]+$", "", re.sub(r"\s+", "", text))


def latex_fingerprint(mj) -> list[tuple[str, str]]:
    """按顺序收集全篇公式（行间 + 行内），带所属块编号，失败时能指出是哪一段。"""
    out: list[tuple[str, str]] = []
    for page_idx, block_index, block in middle.iter_blocks(mj):
        uid = middle.block_id(page_idx, block_index)
        block_type = getattr(block, "type", None)
        if block_type == "equation":
            if isinstance(block.content, str) and block.content.strip():
                out.append((uid, "display:" + lenient_latex(block.content)))
            continue
        spans = middle.spans_of(block)
        if spans:
            for span in spans:
                if getattr(span, "type", None) == "equation_inline":
                    out.append((uid, "inline:" + lenient_latex(str(span.content))))
        for caption in middle.caption_items(block):
            for span in middle.spans_of(caption) or []:
                if getattr(span, "type", None) == "equation_inline":
                    out.append((uid, "inline:" + lenient_latex(str(span.content))))
    return out


def table_signature(mj) -> list[dict]:
    tables: list[dict] = []
    for _, _, block in middle.iter_blocks(mj):
        if getattr(block, "type", None) != "table":
            continue
        body = middle.table_body_item(block)
        markup = body.content if body is not None and isinstance(body.content, str) else ""
        tables.append(
            {
                "scaffold": middle.html_scaffold(markup),
                "cells": markup.count("<td") + markup.count("<th"),
                "rows": markup.count("<tr"),
                "eq": middle.html_eq_fragments(markup),
            }
        )
    return tables


def _count_refs(md: str, name: str) -> int:
    total = 0
    for match in CITE_GROUPED[name].finditer(md):
        for group in match.groups():
            if group:
                total += len(_NUM_RE.findall(group))
    return total


def citation_counts(md: str) -> dict[str, int]:
    """英文写法与中文写法合并计数，支持 `Figs. 5 and 6`、`(Figure 1 and 2)`、`图 1、图 2`。

    旧实现只取每组第一个图号，导致「英文省略写法」少计、「中文拆开写」多计，
    翻得越忠实越容易挂 QA。
    """
    return {
        "bracket": _count_refs(md, "bracket"),
        "fig": _count_refs(md, "fig") + _count_refs(md, "fig_zh"),
        "table": _count_refs(md, "table") + _count_refs(md, "table_zh"),
        "eq": _count_refs(md, "eq") + _count_refs(md, "eq_zh"),
    }


def strip_math(text: str) -> str:
    """把行间/行内公式连同 LaTeX 命令名一起剥掉。

    实测教训：只剥 $$…$$ 时，行内 $\\circ$、$\\mathrm{...}$ 里的命令名会被
    当成英文散文，公式密集的论文必然误报 12%，而「重翻」根本无从下手。
    """
    text = DOLLAR_BLOCK_RE.sub(" ", text)
    text = re.sub(r"\\begin\{.*?\}.*?\\end\{.*?\}", " ", text, flags=re.S)
    text = re.sub(r"\\text\{[^{}]*\}", " ", text)
    text = re.sub(r"(?<!\$)\$[^$]*\$(?!\$)", " ", text)
    text = re.sub(r"\\(?:[A-Za-z]+\*?)", " ", text)
    return text


def english_residue(md: str, glossary: dict[str, str]) -> dict[str, float]:
    """估英文残留：剥掉公式后，只看「小写开头的英文词」，专有名词与术语白名单不算。

    整篇英文段落一定会带 the/of/and 这类词，而 Transformer、BLEU、作者名不会，
    所以这个口径比「数字母」更贴近「这段到底翻没翻」。
    """
    text = strip_math(md)
    text = IMG_RE.sub(" ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = URL_RE.sub(" ", text)
    for term in sorted(glossary, key=len, reverse=True):
        text = re.sub(re.escape(term), " ", text, flags=re.I)
    latin = sum(len(word) for word in PROSE_WORD_RE.findall(text))
    cjk = sum(1 for ch in text if CJK_RE.match(ch))
    total = latin + cjk
    return {"latin": latin, "cjk": cjk, "ratio": (latin / total) if total else 0.0}


def language_offenders(zh_mj, glossary: dict[str, str], limit: int = 5) -> list[str]:
    """找出真正残留英文的段落编号，别只丢一个百分比让人自己猜。"""
    offenders: list[tuple[int, str]] = []
    for unit in middle.iter_units(zh_mj):
        plain = strip_math(unit.source)
        if re.search(r"[\w.+-]+@[\w-]+\.[\w.]+|https?://", plain):
            continue
        words = PROSE_WORD_RE.findall(plain)
        if len(words) < 2:
            continue
        letters = sum(len(word) for word in words)
        if letters >= 12:
            offenders.append((letters, f"{unit.uid}（约 {len(words)} 个英文词：{' '.join(words[:4])}…）"))
    offenders.sort(reverse=True)
    return [item for _, item in offenders[:limit]]


def citation_unit_diff(workdir: Path, limit: int = 6) -> list[str]:
    """逐段比对引用计数，指出到底是哪几段对不上。

    只给总数（28 → 29）没法定位；这里能直接报 `p7b3 {'fig': (2, 1)}`。
    """
    import json as _json

    chunks_path = workdir / "chunks.json"
    trans_path = workdir / "translations.jsonl"
    if not chunks_path.is_file() or not trans_path.is_file():
        return []
    try:
        chunks = _json.loads(chunks_path.read_text(encoding="utf-8"))
    except _json.JSONDecodeError:
        return []
    zh_map: dict[str, str] = {}
    for line in trans_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("uid"):
            zh_map[record["uid"]] = str(record.get("zh") or "")
    diffs: list[str] = []
    for chunk in chunks.get("chunks", []):
        for unit in chunk.get("units", []):
            uid = unit.get("uid")
            zh = zh_map.get(uid)
            if not zh:
                continue
            english = citation_counts(str(unit.get("source") or ""))
            chinese = citation_counts(zh)
            delta = {k: (english[k], chinese[k]) for k in english if english[k] != chinese[k]}
            if delta:
                diffs.append(f"{uid} {delta}")
                if len(diffs) >= limit:
                    return diffs
    return diffs


def read_glossary(workdir: Path) -> dict[str, str]:
    path = workdir / "glossary.csv"
    if not path.is_file():
        return {}
    import csv

    terms: dict[str, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle):
            if len(row) >= 2 and row[0].strip():
                terms[row[0].strip()] = row[1].strip()
    return terms


def strip_references(md: str) -> str:
    """参考文献之后的部分不参与语言检查。"""
    for marker in ("## References", "# References", "References\n", "参考文献"):
        index = md.rfind(marker)
        if index > 0:
            return md[:index]
    return md


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="质检：三道闸门 + 引用 + 语言",
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv[1:])

    paths.setup_stdout()
    workdir = Path(paths.clean_path(args.workdir)).expanduser().resolve()
    paths.set_log_file(workdir / "run.log")

    source_dir = workdir / "source"
    source_md = read(source_dir / "markdown.md")
    zh_path = workdir / "中文版.md"
    if not zh_path.is_file():
        zh_path = workdir / "zh.md"
    zh_md = read(zh_path)
    if not source_md:
        paths.fail(paths.EXIT_PARSE_FAILED, "找不到原始解析结果 source/markdown.md",
                   "先跑解析（prepare）。")
    if not zh_md:
        paths.fail(paths.EXIT_QA_FAILED, "还没有中文版.md（或旧名 zh.md），没法质检", "先跑合并（merge）。")

    source_mj = middle.load_middle(source_dir / "middle_json.json")

    # 中间文件只是「更严格校验」的素材，坏了不能让整个质检崩掉：
    # 降级为 markdown 侧检查，并把降级原因写进 qa.json。
    issues: list[str] = []
    notes: list[str] = []
    zh_mj = None
    zh_middle_path = workdir / "middle_json.zh.json"
    if zh_middle_path.is_file():
        try:
            zh_mj = middle.load_middle(zh_middle_path)
        except Exception as exc:  # pydantic 校验失败、文件半写等
            notes.append(f"中间文件读不出来，已降级为 markdown 侧校验：{type(exc).__name__}: {str(exc)[:120]}")
    else:
        notes.append("没有 middle_json.zh.json，公式只做了 markdown 侧计数校验")

    gates: dict[str, dict] = {}

    # ---- 公式
    src_formula = formula_stats(source_md)
    zh_formula = formula_stats(zh_md)
    formula_ok = src_formula == zh_formula
    detail: dict = {"ok": formula_ok, "source": src_formula, "zh": zh_formula}
    if not formula_ok:
        issues.append(
            f"公式数量不一致：原文 {src_formula['display_blocks']} 个行间 / "
            f"{src_formula['inline_dollars']} 个行内；译文 {zh_formula['display_blocks']} / "
            f"{zh_formula['inline_dollars']}"
        )
    # 数量对了还不够：公式内容被换掉时数量不变，必须逐条比 LaTeX
    if zh_mj is not None:
        src_latex = latex_fingerprint(source_mj)
        zh_latex = latex_fingerprint(zh_mj)
        latex_ok = src_latex == zh_latex
        detail["latex_ok"] = latex_ok
        detail["latex_blocks"] = len(src_latex)
        if not latex_ok:
            formula_ok = False
            detail["ok"] = False
            mismatch: list[str] = []
            for index, ((src_uid, left), (zh_uid, right)) in enumerate(zip(src_latex, zh_latex)):
                if left != right:
                    mismatch.append(
                        f"第 {index + 1} 个公式（块 {src_uid}）：原文 {left!r} / 译文 {right!r}"
                    )
            if mismatch:
                detail["mismatches"] = len(mismatch)
                issues.append(
                    f"{len(mismatch)} 个公式的 LaTeX 与原文不一致：" + "；".join(mismatch[:5])
                    + ("…（还有更多）" if len(mismatch) > 5 else "")
                )
            if len(src_latex) != len(zh_latex):
                issues.append(
                    f"公式条目数变了：原文 {len(src_latex)} 条、译文 {len(zh_latex)} 条"
                    f"（多出的块：{[uid for uid, _ in (zh_latex[len(src_latex):] if len(zh_latex) > len(src_latex) else src_latex[len(zh_latex):])][:5]}）"
                )
    else:
        # 不在 issues 里记失败：降级是「少做了一项更严格的检查」，不是稿子坏了
        detail["latex_ok"] = None
        detail["degraded"] = True
    gates["formula"] = detail

    # ---- 图片
    src_images = image_paths(source_md)
    zh_images = image_paths(zh_md)
    image_ok = src_images == zh_images
    if not image_ok:
        missing = sorted(src_images - zh_images)
        extra = sorted(zh_images - src_images)
        issues.append(f"图片引用被改：少了 {missing[:5]}，多了 {extra[:5]}")
    gates["image"] = {"ok": image_ok, "source": len(src_images), "zh": len(zh_images)}

    # ---- 表格
    src_tables = table_signature(source_mj)
    zh_tables = table_signature(zh_mj) if zh_mj else []
    table_ok = src_tables == zh_tables
    if zh_tables and not table_ok:
        issues.append(f"表格结构被改：原文 {len(src_tables)} 张，译文 {len(zh_tables)} 张")
    gates["table"] = {"ok": table_ok or not zh_tables, "tables": len(src_tables)}

    # ---- 引用
    src_cites = citation_counts(source_md)
    zh_cites = citation_counts(zh_md)
    cite_ok = src_cites == zh_cites
    cite_units = citation_unit_diff(workdir)
    if not cite_ok:
        diff = {k: (src_cites[k], zh_cites[k]) for k in src_cites if src_cites[k] != zh_cites[k]}
        detail_text = f"，可疑段落：{'；'.join(cite_units)}" if cite_units else "（未能定位到具体段落）"
        issues.append(f"交叉引用次数变了：{diff}{detail_text}")
    gates["citation"] = {"ok": cite_ok, "source": src_cites, "zh": zh_cites, "uids": cite_units}

    # ---- 语言
    glossary = read_glossary(workdir)
    residue = english_residue(strip_references(zh_md), glossary)
    language_ok = residue["ratio"] <= 0.05
    offenders = language_offenders(zh_mj, glossary) if zh_mj is not None else []
    if not language_ok:
        hint = "；".join(offenders) if offenders else "没定位到具体段落，可能是全文级问题"
        issues.append(f"译文里英文残留 {residue['ratio']:.1%}，超过 5% 上限。可疑段落：{hint}")
    gates["language"] = {"ok": language_ok, **residue, "offenders": offenders}

    payload = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ok": not issues,
        "gates": gates,
        "issues": issues,
        "notes": notes,
    }
    (workdir / "qa.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        for name, gate in gates.items():
            paths.say(f"[质检] {name:<10} {'通过' if gate.get('ok') else '不通过'}")
            for key in ("source", "zh", "tables"):
                if key in gate:
                    paths.say(f"          {key}: {gate[key]}")
        for note in notes:
            paths.say(f"[质检] 注意：{note}")
        if issues:
            paths.say("[质检] 发现的问题：")
            for item in issues:
                paths.say(f"   - {item}")
            paths.say("[质检] 结论：不通过。请把上面几段重新翻译后再跑一次。")
        else:
            paths.say("[质检] 结论：全部通过。")

    return paths.EXIT_OK if not issues else paths.EXIT_QA_FAILED


if __name__ == "__main__":
    sys.exit(main(sys.argv))



