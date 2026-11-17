#!/usr/bin/env python3
"""middle_json 读写的共用工具。

MinerU 4.2 的 middle_json 是 pydantic 严格模型（docvortex.schema.MiddleJson），
结构是 pages[].blocks[].content[]，其中 content 可能是：
  - 行内条目列表（text / equation_inline / hyperlink ...）
  - 一个 HTML 字符串（table_body）
  - 一个 LaTeX 字符串（equation）

这里集中处理「哪些块要翻」「怎么拼成原文」「怎么把译文写回去」，
seg / merge / qa 都只调这一层，避免三处各写一套规则。

翻译单元（Unit）的粒度：
  - 纯文字段：整个块一次翻完，行内公式拼成 $...$ 保留在原位，译文再按 $...$ 拆回去；
  - 块里夹了超链接等特殊条目：按「连续的文字/公式段」分段翻，特殊条目原样留在中间；
  - 表格：只翻 HTML 里的文本节点，标签骨架一个字都不动（QA 会比对骨架）。
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

# 会翻译的块（按提示词 4.3）
TRANSLATABLE_BLOCK_TYPES = {
    "text": "paragraph",
    "paragraph_title": "heading",
    "doc_title": "heading",
    "page_footnote": "footnote",
}

# 目录（index）与列表（list）的正文在**嵌套子块**里，单独处理
NESTED_TEXT_BLOCK_TYPES = {"index": "list_item", "list": "list_item"}

# 明确不翻译、也不许碰的块
SKIP_BLOCK_TYPES = {
    "equation",      # 行间公式：可能是 LaTeX，也可能只有图片兜底，一个字节都不许动
    "ref_text",      # 参考文献条目
    "page_number",   # 页码（不渲染）
    "header",        # 页眉（不渲染）
    "aside_text",    # 侧边水印（不渲染）
    "footer",        # 页脚（不渲染）
    "code",          # 代码块
    "algorithm",     # 算法伪代码
}

# 这些块里的 caption 要翻，但块本体（图片/表格）不动
VISUAL_BLOCK_TYPES = {"image", "chart", "table"}

CAPTION_ITEM_TYPES = {"image_caption", "table_caption", "chart_caption", "table_footnote"}

# 参与「整段翻译」的行内条目类型
TRANSLATABLE_SPAN_TYPES = {"text", "equation_inline"}


@dataclass
class Unit:
    """一个翻译单元：一段可以独立翻译、独立回填的文字。"""

    uid: str
    page: int
    block_type: str
    kind: str
    source: str
    formulas: list[str] = field(default_factory=list)
    chars: int = 0
    group: int = -1      # 块内第几个文字段（-1 表示整个块 / 表格单元格）
    start: int = -1      # 在行内条目列表中的起止下标（含头不含尾）
    end: int = -1
    continues_prev: bool = False   # 与上一段是同一句（PDF 分页/分块切断）
    continues_next: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "page": self.page,
            "block_type": self.block_type,
            "kind": self.kind,
            "source": self.source,
            "formulas": self.formulas,
            "chars": self.chars,
            "group": self.group,
            "start": self.start,
            "end": self.end,
            "continues_prev": self.continues_prev,
            "continues_next": self.continues_next,
        }


# ---------------------------------------------------------------- 载入 / 保存


def load_raw(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_middle(raw_or_path):
    """返回 pydantic MiddleJson；传入 dict 或路径都可以。"""
    from mineru.types import MiddleJson

    raw = load_raw(raw_or_path) if isinstance(raw_or_path, Path) else raw_or_path
    return MiddleJson.model_validate(raw)


def dump_middle(mj, path: Path) -> None:
    """序列化写回。

    必须 skip_defaults=True：全量序列化会给嵌套子块补上 `continues_prev: null`，
    而 schema 的 `_validate_page_tree` 明确禁止嵌套块携带该字段，
    回读时直接 `ValidationError`（实测：含目录的论文 finish 必崩）。
    """
    payload = mj.to_dict(skip_defaults=True) if hasattr(mj, "to_dict") else mj.model_dump(mode="json", exclude_defaults=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


# 图片默认显示宽度：按 bbox 相对页宽折算成像素，避免预览里铺满一整屏。
# A4 宽 8.27in ≈ 794px（96dpi），两栏论文的单个图约 0.3–0.5 页宽。
PAGE_WIDTH_PX = 794
MIN_IMAGE_PX = 140
MAX_IMAGE_PX = 760


def _image_source(block: Any) -> str:
    """img 块/图表块/公式兜底块里取图片路径。"""
    direct = getattr(block, "image_path", None)
    if isinstance(direct, str) and direct:
        return direct
    for item in getattr(block, "content", None) or []:
        path = getattr(item, "image_path", None)
        if isinstance(path, str) and path:
            return path
    return ""


def make_image_renderer():
    """返回给 MarkdownRenderOptions 的图片回调。

    默认输出 `<img width=…>`：宽度按块 bbox 的页宽占比折算，和 PDF 里看到的比例一致。
    预览器不会再把一张半栏图放大成整屏。设 PDF2ZH_PLAIN_IMAGES=1 可退回 `![](...)`。
    """
    import html as _html
    import os

    plain = os.environ.get("PDF2ZH_PLAIN_IMAGES", "").strip() not in ("", "0", "false")

    def renderer(block: Any) -> str:
        source = _image_source(block)
        if not source:
            return ""
        if plain:
            return f"![]({source})"
        width = 0.0
        bbox = getattr(block, "bbox", None)
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            try:
                width = float(bbox[2]) - float(bbox[0])
            except (TypeError, ValueError):
                width = 0.0
        if width <= 0:
            width = 0.8
        pixels = int(round(width * PAGE_WIDTH_PX))
        pixels = max(MIN_IMAGE_PX, min(MAX_IMAGE_PX, pixels))
        # alt 留空：图注里常带 $…$ 与「图 3」这类引用，
        # 塞进 alt 会被公式/引用闸门重复计数（实测 fig 29→32、行内 $ 90→92）。
        return f'<img src="{_html.escape(source, quote=True)}" width="{pixels}" alt="">'

    return renderer


SUBFIGURE_RE = re.compile(r"^[（(]\s*[a-zA-Z0-9]{1,3}\s*[)）]$")


def _is_subfigure_caption(item: Any) -> bool:
    """`(a)` 这类子图注。译文里可能被写成「子图（a）」，所以两个括号都认。"""
    if getattr(item, "type", None) not in {"image_caption", "chart_caption"}:
        return False
    spans = spans_of(item) or []
    text, _ = build_source(spans)
    return bool(SUBFIGURE_RE.match(text.strip()))


def _visual_bbox(block: Any) -> tuple[float, float, float, float] | None:
    raw = getattr(block, "bbox", None)
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        return tuple(float(value) for value in raw)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def _grid_html(cells: list[dict]) -> str:
    """把「同一页、成行成列」的子图排成表格，保持原文的 (a)(b) / (c)(d) 布局。"""
    import html as _html

    rows: list[list[dict]] = []
    for cell in sorted(cells, key=lambda item: (item["top"], item["left"])):
        if rows and abs(cell["top"] - rows[-1][0]["top"]) <= 0.02:
            rows[-1].append(cell)
        else:
            rows.append([cell])

    parts = ["<table>"]
    for row in rows:
        parts.append("<tr>")
        for cell in sorted(row, key=lambda item: item["left"]):
            width = max(MIN_IMAGE_PX, min(MAX_IMAGE_PX, int(round(cell["width"] * PAGE_WIDTH_PX))))
            label = _html.escape(cell["label"], quote=True)
            parts.append(
                f'<td align="center" valign="top">'
                f'<img src="{_html.escape(cell["src"], quote=True)}" width="{width}" alt="">'
                f'<br>{label}</td>'
            )
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def detect_figure_grids(reference) -> dict[tuple[int, int], dict]:
    """用**原文**模型识别多子图组，返回 {(page_idx, block_index): {...}}。

    必须基于原文：译文可能把 `(a)` 写成「子图（a）」，拿译文去认会漏。
    """
    plan: dict[tuple[int, int], dict] = {}
    for page in reference.pages:
        blocks = list(page.blocks or [])
        index = 0
        while index < len(blocks):
            block = blocks[index]
            if getattr(block, "type", None) not in {"image", "chart"}:
                index += 1
                continue
            members: list[tuple[int, Any]] = []
            cursor = index
            while cursor < len(blocks):
                candidate = blocks[cursor]
                if getattr(candidate, "type", None) not in {"image", "chart"}:
                    break
                captions = [item for item in (candidate.content or [])
                            if getattr(item, "type", None) in {"image_caption", "chart_caption"}]
                if not any(_is_subfigure_caption(item) for item in captions):
                    break
                members.append((cursor, candidate))
                cursor += 1
            if len(members) >= 2:
                cells: list[dict] = []
                for _, member in members:
                    bbox = _visual_bbox(member)
                    src = _image_source(member)
                    if bbox is None or not src:
                        cells = []
                        break
                    label = ""
                    for item in member.content or []:
                        if _is_subfigure_caption(item):
                            label, _ = build_source(spans_of(item) or [])
                            break
                    cells.append({
                        "src": src,
                        "label": label.strip(),
                        "left": bbox[0],
                        "top": bbox[1],
                        "width": max(bbox[2] - bbox[0], 0.05),
                    })
                if cells:
                    grid = _grid_html(cells)
                    for offset, (member_index, _) in enumerate(members):
                        plan[(page.page_idx, member_index)] = {
                            "html": grid if offset == len(members) - 1 else "",
                        }
            index = max(cursor, index + 1)
    return plan


def prepare_image_layout(mj, *, reference=None, enabled: bool = True):
    """把原文识别出的多子图组套到目标模型上；返回 (renderer, 统计)。

    两处坑：
      1. `render()` 内部会重建 block 对象，按 id() 映射会失效 —— 用图片路径做键；
      2. 译文可能把子图注 (a) 写成「子图（a）」，所以短图注一律视为子图注。
    """
    renderer = make_image_renderer()
    stats = {"groups": 0, "cells": 0}
    if not enabled:
        return renderer, stats

    plan = detect_figure_grids(reference if reference is not None else mj)
    if not plan:
        return renderer, stats

    by_source: dict[str, str] = {}
    for page_idx, block_index, block in iter_blocks(mj):
        entry = plan.get((page_idx, block_index))
        if entry is None:
            continue
        source = _image_source(block)
        if source:
            by_source[source] = entry["html"]
            stats["cells"] += 1

        kept = []
        for item in block.content or []:
            if getattr(item, "type", None) not in {"image_caption", "chart_caption"}:
                kept.append(item)
                continue
            text, _ = build_source(spans_of(item) or [])
            label = text.strip()
            # 子图注：`(a)` 或短标签；总图注（Figure 7 …）保留
            if _is_subfigure_caption(item) or len(label) <= 8:
                continue
            kept.append(item)
        block.content = kept

    stats["groups"] = len({entry["html"] for entry in plan.values() if entry["html"]})

    def grid_renderer(block: Any) -> str:
        source = _image_source(block)
        html = by_source.get(source)
        if html is not None:
            return html
        return renderer(block)

    return grid_renderer, stats


def render_markdown(mj, *, image_renderer=None) -> str:
    """用 MinerU 官方渲染器出 markdown（禁止自己拼字符串）。"""
    from mineru.render import MarkdownRenderOptions, RenderFormat, render

    options = MarkdownRenderOptions(image_renderer=image_renderer) if image_renderer else None
    return render(mj, RenderFormat.MARKDOWN, options=options) if options else render(mj, RenderFormat.MARKDOWN)


# ---------------------------------------------------------------- 结构遍历


def block_id(page_idx: int, block_index: int) -> str:
    return f"p{page_idx}b{block_index}"


def iter_blocks(mj) -> Iterator[tuple[int, int, Any]]:
    for page in mj.pages:
        for index, block in enumerate(page.blocks or []):
            yield page.page_idx, index, block


def _text_of(content: Any) -> str:
    return content if isinstance(content, str) else ""


def spans_of(item: Any) -> list[Any] | None:
    """取「行内条目列表」型 content；不是这种结构就返回 None。"""
    value = getattr(item, "content", None)
    return value if isinstance(value, list) else None


def caption_items(block: Any) -> list[Any]:
    """图片/表格/图表块里的 caption 子块。"""
    return [
        item
        for item in (block.content or [])
        if getattr(item, "type", None) in CAPTION_ITEM_TYPES
    ]


def table_body_item(block: Any) -> Any | None:
    for item in block.content or []:
        if getattr(item, "type", None) == "table_body":
            return item
    return None


# ---------------------------------------------------------------- 原文拼装


def collapse_spaces(text: str) -> str:
    """PDF 抽出来的换行/多空格在中文里没有意义，统一压成单空格。"""
    text = text.replace("\u00ad", "")
    return re.sub(r"[ \t\r\n]+", " ", text).strip()


def span_groups(spans: list[Any]) -> list[tuple[int, int]]:
    """把行内条目切成「连续可翻译段」，返回 [start, end) 列表。"""
    groups: list[tuple[int, int]] = []
    start: int | None = None
    for index, span in enumerate(spans):
        translatable = getattr(span, "type", None) in TRANSLATABLE_SPAN_TYPES
        if translatable and start is None:
            start = index
        elif not translatable and start is not None:
            groups.append((start, index))
            start = None
    if start is not None:
        groups.append((start, len(spans)))
    return groups


_NO_SPACE_BEFORE = set("，。、；：！？）】》’”%,.;:!?)]}")
_NO_SPACE_AFTER = set("（【《‘“([{")


def _is_cjk(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f" or "\uff00" <= ch <= "\uffef"


def _needs_space(left: str, right: str) -> bool:
    """两段文字直接拼接时是否要补空格。

    MinerU 的行内条目通常不带首尾空格，直接 "".join 会把
    `where` + `$V(t)$` + `is` 粘成 `where$V(t)$is`：
    英文读不了，而且有些 Markdown 数学扩展会因为 `$` 紧贴字母而解析失败。
    """
    if not left or not right:
        return False
    if left[-1].isspace() or right[0].isspace():
        return False
    if _is_cjk(left[-1]) or _is_cjk(right[0]):
        return False
    if left[-1] in _NO_SPACE_AFTER or right[0] in _NO_SPACE_BEFORE:
        return False
    return True


def _join_spans(parts: list[str]) -> str:
    out = ""
    for part in parts:
        if not part:
            continue
        if out and _needs_space(out, part):
            out += " "
        out += part
    return out


def build_source(spans: list[Any]) -> tuple[str, list[str]]:
    """把一段行内条目拼成「带 $公式$ 的纯文本」，并返回公式清单。"""
    parts: list[str] = []
    formulas: list[str] = []
    for span in spans:
        kind = getattr(span, "type", None)
        content = _text_of(getattr(span, "content", ""))
        if kind == "text":
            parts.append(content)
        elif kind == "equation_inline":
            latex = content.strip()
            formulas.append(latex)
            parts.append(f"${latex}$")
    return collapse_spaces(_join_spans(parts)), formulas


def split_formulas(text: str) -> list[str]:
    """从译文里按 $...$ 抽出公式（用于校验与回填）。"""
    return [m.group(1).strip() for m in re.finditer(r"\$([^$]+)\$", text)]


# ---------------------------------------------------------------- 译文回填


def make_span(kind: str, content: str):
    """构造行内条目对象；依赖 docvortex 的 schema 类。"""
    from docvortex.schema import EquationInlineSpan, TextSpan

    if kind == "equation_inline":
        return EquationInlineSpan(type="equation_inline", content=content)
    return TextSpan(type="text", content=content)


def rebuild_spans(zh_text: str) -> list[Any]:
    """把带 $公式$ 的译文重新拆成 text / equation_inline 条目。

    两个坑（都是实测踩到的）：
      1. 原文是 `$A$ $B$` 时，中间的纯空格 span 如果被 strip 掉，渲染后就粘成
         `$A$$B$`，会被当成行间公式，QA 误报「公式数量不一致」；
      2. 相邻两个公式之间没有文字时，渲染器同样会粘连。
    所以：空白 span 保留成一个空格，相邻公式之间补一个空格。
    """
    spans: list[Any] = []
    cursor = 0

    def text_span(raw: str) -> Any:
        """保留与公式相邻的那一个空格。

        英文段落里 `where $V(t)$ is` 的空格一旦被 strip 掉，
        渲染出来就是 `where$V(t)$is`：英文粘成一坨，
        部分 Markdown 数学扩展还会因为 `$` 紧贴字母而放弃解析。
        """
        if not raw.strip():
            return make_span("text", " ")
        lead = " " if raw[0].isspace() and spans else ""
        trail = " " if raw[-1].isspace() else ""
        return make_span("text", f"{lead}{raw.strip()}{trail}")

    for match in re.finditer(r"\$([^$]+)\$", zh_text):
        gap = zh_text[cursor:match.start()]
        if gap:
            spans.append(text_span(gap))
        spans.append(make_span("equation_inline", match.group(1).strip()))
        cursor = match.end()
    tail = zh_text[cursor:]
    if tail.strip():
        spans.append(text_span(tail))

    # 相邻公式之间补空格，避免被渲染成 $$ 粘连
    spaced: list[Any] = []
    for span in spans:
        if (spaced
                and getattr(spaced[-1], "type", None) == "equation_inline"
                and getattr(span, "type", None) == "equation_inline"):
            spaced.append(make_span("text", " "))
        spaced.append(span)
    return spaced


def rebuild_from_groups(spans: list[Any], groups: list[tuple[int, int]],
                        translations: dict[int, str], *, bilingual: bool = False) -> list[Any]:
    """按分组把译文写回条目列表；未翻译的组保留原文。"""
    out: list[Any] = []
    cursor = 0
    for group_index, (start, end) in enumerate(groups):
        out.extend(spans[cursor:start])
        zh = translations.get(group_index)
        if zh is None:
            out.extend(spans[start:end])
        else:
            if bilingual:
                english, _ = build_source(spans[start:end])
                zh = f"{english}\n\n{zh}"
            out.extend(rebuild_spans(zh))
        cursor = end
    out.extend(spans[cursor:])
    return out


# ---------------------------------------------------------------- 表格 HTML


TAG_RE = re.compile(r"(<[^>]+>)")
LATEXISH_RE = re.compile(r"[\\^_{}]")


def html_units(markup: str) -> list[tuple[int, str]]:
    """返回 (文本节点下标, 文本)；空白、纯数字、无英文、<eq> 里的公式都跳过。

    表格里会出现 `<td><eq>d_{model}</eq></td>` 这种带公式的单元格，
    公式文本绝不能送去翻译，否则一翻就坏。
    """
    parts = TAG_RE.split(markup)
    out: list[tuple[int, str]] = []
    inside_eq = 0
    for index, part in enumerate(parts):
        if index % 2 == 1:  # 标签
            tag = part.lower()
            if tag.startswith("<eq"):
                inside_eq += 1
            elif tag.startswith("</eq"):
                inside_eq = max(0, inside_eq - 1)
            continue
        if inside_eq:
            continue
        text = collapse_spaces(html.unescape(part))
        if not text or not re.search(r"[A-Za-z]{2,}", text):
            continue
        if LATEXISH_RE.search(text):  # 看起来是公式残片，别碰
            continue
        out.append((index, text))
    return out


def html_eq_fragments(markup: str) -> list[str]:
    """表格里 <eq>…</eq> 的公式清单，QA 用它证明单元格公式没被改。"""
    return [re.sub(r"\s+", "", item) for item in re.findall(r"<eq>(.*?)</eq>", markup, re.S)]


def replace_html_text(markup: str, replacements: dict[int, str]) -> str:
    """按节点下标替换文本节点，标签原样保留。"""
    parts = TAG_RE.split(markup)
    for index, value in replacements.items():
        if index < len(parts) and index % 2 == 0:
            parts[index] = html.escape(value)
    return "".join(parts)


def html_scaffold(markup: str) -> str:
    """标签骨架指纹：QA 用它证明表格结构没被改。"""
    return "".join(part for index, part in enumerate(TAG_RE.split(markup)) if index % 2 == 1)


# ---------------------------------------------------------------- 单元枚举


def _group_uid(base: str, group_index: int, total_groups: int, whole_block: bool) -> str:
    if whole_block and total_groups == 1:
        return base
    return f"{base}#r{group_index}"


def units_for_spans(base: str, page: int, block_type: str, kind: str,
                    spans: list[Any]) -> list[Unit]:
    groups = span_groups(spans)
    units: list[Unit] = []
    for group_index, (start, end) in enumerate(groups):
        source, formulas = build_source(spans[start:end])
        if not source:
            continue
        uid = _group_uid(base, group_index, len(groups), whole_block=True)
        units.append(Unit(uid, page, block_type, kind, source, formulas, len(source),
                          group_index, start, end))
    return units


def running_header_keys(mj) -> set[tuple[int, int]]:
    """找出「每页都重复、位于页面顶/底部的短文本」——被 MinerU 误判成 text 的页眉页脚。

    实测：`Accepted Version of:` 这类行在 37 页里出现 27 次、全被当正文翻译，
    白烧 token 还污染译文。
    """
    seen: dict[str, list[tuple[int, int]]] = {}
    for page in mj.pages:
        for index, block in enumerate(page.blocks or []):
            if getattr(block, "type", None) not in {"text", "header", "footer"}:
                continue
            spans = spans_of(block)
            if not spans:
                continue
            text, _ = build_source(spans)
            if not (6 <= len(text) <= 120):
                continue
            bbox = getattr(block, "bbox", None)
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                continue
            try:
                top, bottom = float(bbox[1]), float(bbox[3])
            except (TypeError, ValueError):
                continue
            if top < 0.08 or bottom > 0.92:
                seen.setdefault(text.lower(), []).append((page.page_idx, index))

    keys: set[tuple[int, int]] = set()
    for occurrences in seen.values():
        pages = {page for page, _ in occurrences}
        if len(pages) >= 3:
            keys.update(occurrences)
    return keys


def clear_blocks(mj, keys: set[tuple[int, int]]) -> int:
    """按 (页号, 块序号) 清空内容；用于把重复页眉从成品里去掉。"""
    removed = 0
    for page_idx, index, block in iter_blocks(mj):
        if (page_idx, index) in keys and getattr(block, "content", None):
            block.content = []
            removed += 1
    return removed


def drop_running_headers(mj) -> int:
    """把识别出的重复页眉/页脚清空，避免它们在成品里重复出现。"""
    keys = running_header_keys(mj)
    removed = 0
    for page_idx, index, block in iter_blocks(mj):
        if (page_idx, index) in keys and getattr(block, "content", None):
            block.content = []
            removed += 1
    return removed


TERMINAL_RE = re.compile(r"[.!?。！？；;:：]\s*$")


def _mark_continuations(units: list[Unit]) -> None:
    """标记「上一段没说完、下一段接着说」的段落。

    实测：分页处常把一句话切成两段，翻译时如果不提示，
    agent 会把两半当成两个独立段落翻，读起来断裂。
    """
    for index, unit in enumerate(units[:-1]):
        nxt = units[index + 1]
        if unit.kind != "paragraph" or nxt.kind != "paragraph":
            continue
        if TERMINAL_RE.search(unit.source):
            continue
        if not re.match(r"[a-z]", nxt.source):
            continue
        unit.continues_next = True
        nxt.continues_prev = True


def iter_units(mj) -> Iterator[Unit]:
    """按固定顺序枚举所有需要翻译的单元（seg 与 merge 必须结果一致）。"""
    units = list(_iter_units_raw(mj))
    _mark_continuations(units)
    yield from units


def _iter_units_raw(mj) -> Iterator[Unit]:
    skip_keys = running_header_keys(mj)
    for page_idx, block_index, block in iter_blocks(mj):
        block_type = getattr(block, "type", None)
        if block_type in SKIP_BLOCK_TYPES or (page_idx, block_index) in skip_keys:
            continue
        base = block_id(page_idx, block_index)

        if block_type in TRANSLATABLE_BLOCK_TYPES:
            spans = spans_of(block)
            if spans:
                yield from units_for_spans(base, page_idx, block_type,
                                            TRANSLATABLE_BLOCK_TYPES[block_type], spans)
            continue

        if block_type in NESTED_TEXT_BLOCK_TYPES:
            for child_index, child in enumerate(block.content or []):
                spans = spans_of(child)
                if not spans:
                    continue
                yield from units_for_spans(f"{base}#i{child_index}", page_idx, block_type,
                                            NESTED_TEXT_BLOCK_TYPES[block_type], spans)
            continue

        if block_type in VISUAL_BLOCK_TYPES:
            for caption_index, caption in enumerate(caption_items(block)):
                spans = spans_of(caption)
                if not spans:
                    continue
                yield from units_for_spans(f"{base}#c{caption_index}", page_idx,
                                            block_type, "caption", spans)

            if block_type == "table":
                body = table_body_item(block)
                markup = _text_of(getattr(body, "content", "")) if body is not None else ""
                for node_index, text in html_units(markup):
                    yield Unit(f"{base}#t{node_index}", page_idx, block_type,
                               "table_cell", text, [], len(text))


def unit_map(mj) -> dict[str, Unit]:
    return {unit.uid: unit for unit in iter_units(mj)}


# ---------------------------------------------------------------- 回填整篇


def _apply_spans(base: str, spans: list[Any], translations: dict[str, str],
                 bilingual: bool) -> list[Any]:
    groups = span_groups(spans)
    mapping: dict[int, str] = {}
    for group_index in range(len(groups)):
        uid = _group_uid(base, group_index, len(groups), whole_block=True)
        zh = translations.get(uid)
        if zh:
            mapping[group_index] = zh
    if not mapping:
        return spans
    return rebuild_from_groups(spans, groups, mapping, bilingual=bilingual)


def apply_translations(mj, translations: dict[str, str], *, bilingual: bool = False) -> list[str]:
    """把译文写回中间表示；返回「有原文但没译文」的单元编号。"""
    missing: list[str] = []
    for page_idx, block_index, block in iter_blocks(mj):
        block_type = getattr(block, "type", None)
        base = block_id(page_idx, block_index)

        if block_type in TRANSLATABLE_BLOCK_TYPES:
            spans = spans_of(block)
            if not spans:
                continue
            block.content = _apply_spans(base, spans, translations, bilingual)
            for group_index, (start, end) in enumerate(span_groups(spans)):
                uid = _group_uid(base, group_index, len(span_groups(spans)), whole_block=True)
                if build_source(spans[start:end])[0] and uid not in translations:
                    missing.append(uid)
            continue

        if block_type in NESTED_TEXT_BLOCK_TYPES:
            for child_index, child in enumerate(block.content or []):
                spans = spans_of(child)
                if not spans:
                    continue
                child.content = _apply_spans(f"{base}#i{child_index}", spans, translations, bilingual)
                for group_index, (start, end) in enumerate(span_groups(spans)):
                    uid = _group_uid(f"{base}#i{child_index}", group_index,
                                     len(span_groups(spans)), whole_block=True)
                    if build_source(spans[start:end])[0] and uid not in translations:
                        missing.append(uid)
            continue

        if block_type in VISUAL_BLOCK_TYPES:
            for caption_index, caption in enumerate(caption_items(block)):
                spans = spans_of(caption)
                if not spans:
                    continue
                caption.content = _apply_spans(f"{base}#c{caption_index}", spans,
                                                translations, bilingual)
                for group_index, (start, end) in enumerate(span_groups(spans)):
                    uid = _group_uid(f"{base}#c{caption_index}", group_index,
                                     len(span_groups(spans)), whole_block=True)
                    if build_source(spans[start:end])[0] and uid not in translations:
                        missing.append(uid)

            if block_type == "table":
                body = table_body_item(block)
                markup = _text_of(getattr(body, "content", "")) if body is not None else ""
                if not markup:
                    continue
                replacements: dict[int, str] = {}
                for node_index, _ in html_units(markup):
                    uid = f"{base}#t{node_index}"
                    zh = translations.get(uid)
                    if zh:
                        replacements[node_index] = zh
                    else:
                        missing.append(uid)
                if replacements:
                    body.content = replace_html_text(markup, replacements)
    return missing


# ---------------------------------------------------------------- 杂项


def sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def count_latin(text: str) -> int:
    return sum(1 for ch in text if "a" <= ch.lower() <= "z")
