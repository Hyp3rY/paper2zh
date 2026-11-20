#!/usr/bin/env python3
"""回填译文并用 MinerU 官方渲染器生成 zh.md / bilingual.md。

不允许自己拼 markdown：两份产物都必须是 `mineru.render.render(..., MARKDOWN)` 的结果。

用法：
    python merge.py --workdir <产物目录>
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

# 嵌入式 Python 的 ._pth 会关掉「脚本目录自动进 sys.path」，这里显式登记一次。
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
import middle
import paths

SOURCE_DIR = "source"


TERMINAL_RE = re.compile(r"[.!?。！？；;:：]\s*$")


def _paragraph_source(block, sources: dict | None = None,
                      key: tuple[int, int] | None = None) -> str | None:
    """只处理「整块就是一段纯文字/公式」的块，别的（含链接、题注）不碰。"""
    if getattr(block, "type", None) != "text":
        return None
    spans = middle.spans_of(block)
    if not spans:
        return None
    groups = middle.span_groups(spans)
    if len(groups) != 1:
        return None
    if sources is not None and key is not None:
        return sources.get(key)      # 用**原文**判断断句，不能用已翻译的内容
    return middle.build_source(spans)[0]


def dedupe_author_prefix(mj) -> int:
    """作者行被切成多块、每块都加了「作者：」前缀时，只保留第一个。

    MinerU 经常把作者列表拆成 2–3 个块，agent 为避免「没有中文」被退回，
    会给每块都加 `作者：`。成品里就成了「作者：A 作者：B」。
    """
    changed = 0
    for page in mj.pages:
        for block in page.blocks or []:
            if getattr(block, "type", None) != "text":
                continue
            spans = [sp for sp in (middle.spans_of(block) or [])
                     if getattr(sp, "type", None) == "text"]
            if not spans:
                continue
            joined = "".join(str(sp.content) for sp in spans)
            if not joined.strip().startswith("作者："):
                continue
            seen = False
            for span in spans:
                content = str(span.content)
                if not seen:
                    index = content.find("作者：")
                    if index < 0:
                        continue
                    head, tail = content[: index + 3], content[index + 3:]
                    span.content = head + tail.replace("作者：", " ")
                    seen = True
                else:
                    span.content = content.replace("作者：", " ")
            changed += 1
    return changed


def stitch_continuations(mj, translations: dict[str, str], *, bilingual: bool,
                         sources: dict[tuple[int, int], str] | None = None) -> int:
    """把「上一块没写完、下一块接着写」的段落接回去。

    MinerU 有时会在句子中间切块（上一块以 ``can therefore be`` 结尾，
    下一块以 ``directly inferred`` 开头）。中文版看着像被截断，
    双语版更是英文、中文、英文交替，读起来支离破碎。

    这里只做**显示层拼接**：把连续的块合成一段（英文一段、中文一段），
    不改译文内容、不改公式，也不碰标题、图注、表格。
    """
    order: list[tuple[int, int, object]] = list(middle.iter_blocks(mj))
    merged = 0
    index = 0
    while index < len(order):
        page_idx, block_index, block = order[index]
        source = _paragraph_source(block, sources, (page_idx, block_index))
        if not source:
            index += 1
            continue
        uid = middle.block_id(page_idx, block_index)
        if uid not in translations:
            index += 1
            continue
        group = [(page_idx, block_index, block, source, uid)]
        cursor = index + 1
        while cursor < len(order):
            next_page, next_index, next_block = order[cursor]
            # 页眉、页码、页脚、侧边水印不渲染，直接跳过继续看下一块
            if getattr(next_block, "type", None) == "equation":
                break
            if getattr(next_block, "type", None) in (middle.SKIP_BLOCK_TYPES | {"header"}):
                cursor += 1
                continue
            # 不要跨公式/图片/表格拼接文本块：否则前面的文本会被合成一大段，
            # 公式反而被挤到整段之后。
            if getattr(next_block, "type", None) not in {"text", "caption"}:
                break
            next_source = _paragraph_source(next_block, sources, (next_page, next_index))
            if not next_source:
                break
            next_uid = middle.block_id(next_page, next_index)
            if next_uid not in translations:
                break
            previous_source = group[-1][3]
            starts_lower = bool(re.match(r"[a-z]", next_source))
            if TERMINAL_RE.search(previous_source) or not starts_lower:
                break
            group.append((next_page, next_index, next_block, next_source, next_uid))
            cursor += 1
            if len(group) >= 3:
                break
        if len(group) > 1:
            # 中文之间不插空格，英文/公式之间要插（由 middle._join_spans 判定）
            english = middle._join_spans([item[3] for item in group])
            zh_parts = [translations[item[4]] for item in group]
            # 作者行常被切成多块、每块都被加了「作者：」前缀；接回时只保留第一个
            if zh_parts and zh_parts[0].startswith("作者："):
                zh_parts = [zh_parts[0]] + [
                    re.sub(r"^作者[：:]\s*", "", part) for part in zh_parts[1:]
                ]
            chinese = middle._join_spans(zh_parts)
            text = f"{english}\n\n{chinese}" if bilingual else chinese
            group[0][2].content = middle.rebuild_spans(text)
            for _, _, extra_block, _, _ in group[1:]:
                extra_block.content = []
            merged += len(group) - 1
        index = max(cursor, index + 1)
    return merged


def load_translations(path: Path) -> dict[str, str]:
    table: dict[str, str] = {}
    if not path.is_file():
        return table
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("uid"):
            table[record["uid"]] = record.get("zh", "")
    return table


def export_docx(model, workdir: Path, filename: str, source_dir: Path) -> None:
    """用 MinerU 的 DOCX 渲染器导出一份 Word（公式转 OMML、图片内嵌）。"""
    try:
        from docvortex.render.contracts import DocxRenderOptions
        from mineru.render import RenderFormat, render

        images_dir = workdir / "images"

        def resolve_asset(path: str) -> bytes:
            candidate = images_dir / Path(str(path)).name
            if candidate.is_file():
                return candidate.read_bytes()
            fallback = source_dir / "images" / Path(str(path)).name
            return fallback.read_bytes() if fallback.is_file() else b""

        data = render(model, RenderFormat.DOCX,
                      options=DocxRenderOptions(asset_resolver=resolve_asset))
        (workdir / filename).write_bytes(data)
        paths.say(f"[合并] Word：{filename}（{len(data) / 1024:.0f} KB，含图片与公式）")
    except Exception as exc:  # 导出失败不影响 md
        paths.say(f"[合并] {filename} 导出失败（不影响 Markdown）：{type(exc).__name__}: {str(exc)[:120]}")


def refresh_readme(workdir: Path) -> None:
    """生成「先读我.txt」：把 qa.json 的问题翻成人话，没跑质检就说没跑。"""
    qa_path = workdir / "qa.json"
    qa_lines = "（还没有跑自动检查）"
    if qa_path.is_file():
        try:
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            issues = qa.get("issues", [])
            qa_lines = "自动检查没发现问题。" if not issues else "\n".join(
                f"  - {item}" for item in issues[:8]
            )
        except json.JSONDecodeError:
            pass

    word_files = [name for name in (ZH_DOCX, BI_DOCX, "zh.docx", "bilingual.docx")
                  if (workdir / name).is_file()]
    word_line = "".join(f"  {name:<16}Word 版（公式可编辑、图片内嵌）\n" for name in word_files)
    text = f"""这是什么
----------
这份文件夹是「{workdir.name}」的中文翻译结果。

  zh.md         中文版（公式、图片、表格都在原位）
{word_line}  bilingual.md  中英对照版（一段英文、一段中文；公式和表格只出现一次）
  images\\       正文里引用的图片
  source\\       原始解析结果（source\\markdown.md 是英文原文）
  qa.json       自动检查结果（有没有公式或图片被弄坏）

先看哪个
--------
用记事本或 VS Code 打开 zh.md 就能读。图片和 zh.md 放在同一层，Markdown 阅读器会自动显示。

怎么再翻一篇
------------
在 AI 对话框里输入 /paper2zh，再接一句要求（这样能确定用的是本技能）：

  /paper2zh 把 D:\\论文\\xxx.pdf 翻成中文

AI 回复「正在本地解析」就说明技能已加载。
输入 /paper2zh 没反应：回到安装目录双击「1-开始安装.cmd」，再重启 AI 客户端。

这些地方可能有问题
------------------
{qa_lines}

想做改动
--------
改完 zh.md 直接保存即可；想重新生成，重新跑一次翻译流程。
"""
    (workdir / "先读我.txt").write_text(text, encoding="utf-8")


TAG_RE = re.compile(r"\\tag\*?\{([^{}]*)\}")


def github_safe_math(md: str) -> str:
    """GitHub 的 MathJax 会把 \tag{} 渲染成 mlabeledtr，公式会被挤成竖列。

    输出 Markdown 时把编号改成公式内的普通文本；中间结构与 QA 不受影响。
    """
    return TAG_RE.sub(lambda match: f"\\qquad({match.group(1)})", md)


ZH_OUTPUT = "中文版.md"
BI_OUTPUT = "中英对照.md"
ZH_DOCX = "中文版.docx"
BI_DOCX = "中英对照.docx"


def rename_outputs(workdir: Path) -> list[str]:
    """把内部使用的 zh.md / bilingual.md 换成给人看的中文名。

    只在 finish / export 的所有检查、Word 导出完成后调用，避免 QA 和
    export 找不到旧文件名。先读我.txt 里的文件名也一并替换。
    """
    workdir = Path(workdir)
    pairs = [
        ("zh.md", ZH_OUTPUT),
        ("bilingual.md", BI_OUTPUT),
        ("zh.docx", ZH_DOCX),
        ("bilingual.docx", BI_DOCX),
    ]
    renamed: list[str] = []
    for old_name, new_name in pairs:
        old = workdir / old_name
        new = workdir / new_name
        if not old.is_file():
            continue
        if new.exists():
            new.unlink()
        old.replace(new)
        renamed.append(new_name)

    readme = workdir / "先读我.txt"
    if readme.is_file():
        text = readme.read_text(encoding="utf-8")
        text = text.replace("zh.md", ZH_OUTPUT).replace("bilingual.md", BI_OUTPUT)
        readme.write_text(text, encoding="utf-8")
    return renamed


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="把译文回填进 middle_json 并渲染成中文 markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--workdir", required=True, help="产物目录")
    parser.add_argument("--docx", action="store_true", help="另外导出两份 Word（中文版 / 中英对照）")
    args = parser.parse_args(argv[1:])

    paths.setup_stdout()
    workdir = Path(paths.clean_path(args.workdir)).expanduser().resolve()
    paths.set_log_file(workdir / "run.log")

    source_dir = workdir / SOURCE_DIR
    middle_path = source_dir / "middle_json.json"
    if not middle_path.is_file():
        paths.fail(paths.EXIT_PARSE_FAILED, f"找不到解析结果 {middle_path}",
                   "先跑「准备」这一步把 PDF 解析出来。")

    translations = load_translations(workdir / "translations.jsonl")
    if not translations:
        paths.fail(paths.EXIT_TRANSLATOR_UNAVAILABLE, "还没有任何译文",
                   "先按 SKILL.md 的流程翻译，再用 apply 提交。")

    zh_mj = middle.load_middle(middle_path)
    missing = middle.apply_translations(zh_mj, translations)
    if missing:
        paths.say(f"[合并] 还有 {len(missing)} 段没翻，例如：{', '.join(missing[:5])}")
        paths.fail(paths.EXIT_QA_FAILED, "翻译没做完，不能出稿",
                   "继续翻译剩下的段落，或者用 --allow-partial 出半成品（不推荐）。")

    # 质检必须看「未拼接」的中间表示：接回只在显示层发生，
    # 否则公式顺序会被接回打乱，QA 误报「公式被改」（实测 16 处假阳性）。
    middle.dump_middle(zh_mj, workdir / "middle_json.zh.json")

    # 被 MinerU 误判成正文的重复页眉（如 "Accepted Version of:"）：
    # 识别出来后在两个模型里清空，既不再翻译也不再重复出现在成品里。
    reference_model = middle.load_middle(middle_path)
    header_keys = middle.running_header_keys(reference_model)
    dropped_headers = middle.clear_blocks(zh_mj, header_keys)
    dedupe_author_prefix(zh_mj)

    import translate as translate_mod
    glossary = translate_mod.read_glossary(workdir)
    reference_for_cleanup = middle.load_middle(middle_path)
    terms_fixed = middle.enforce_glossary(zh_mj, glossary, reference_for_cleanup)
    authors_fixed = middle.fix_author_lines(zh_mj, reference_for_cleanup)
    hyphens_fixed = middle.tidy_punctuation(zh_mj)
    if terms_fixed or authors_fixed or hyphens_fixed:
        paths.say(f"[合并] 统一术语 {terms_fixed} 处；作者行保留原名 {authors_fixed} 处；"
                  f"中文连字符 {hyphens_fixed} 处")
    if dropped_headers:
        paths.say(f"[合并] 去掉重复页眉/页脚 {dropped_headers} 处")

    import os
    grid_enabled = os.environ.get("PAPER2ZH_FIGURE_GRID", "").strip() not in ("0", "false", "no")
    stitch_enabled = os.environ.get("PAPER2ZH_STITCH", "").strip() not in ("0", "false", "no")
    source_texts: dict[tuple[int, int], str] = {}
    if stitch_enabled:
        reference_model = middle.load_middle(middle_path)
        for page_idx, block_index, block in middle.iter_blocks(reference_model):
            spans = middle.spans_of(block)
            if spans and len(middle.span_groups(spans)) == 1:
                source_texts[(page_idx, block_index)] = middle.build_source(spans)[0]
    merged_zh = (stitch_continuations(zh_mj, translations, bilingual=False, sources=source_texts)
                 if stitch_enabled else 0)
    # 用原文识别多子图组：译文里的 (a) 可能已被翻成「子图（a）」
    reference_mj = middle.load_middle(middle_path)
    moved = middle.relocate_main_captions(zh_mj)
    if moved:
        paths.say(f"[合并] 图注归位：{moved} 处")
    zh_renderer, grid_stats = middle.prepare_image_layout(
        zh_mj, reference=reference_mj, enabled=grid_enabled)
    zh_md = middle.render_markdown(zh_mj, image_renderer=zh_renderer)
    md_replacements = middle.glossary_replacements(glossary)
    zh_md, md_fixed = middle.replace_terms_in_text(zh_md, md_replacements)

    bilingual_mj = middle.load_middle(middle_path)
    middle.apply_translations(bilingual_mj, translations, bilingual=True)
    middle.clear_blocks(bilingual_mj, header_keys)
    dedupe_author_prefix(bilingual_mj)
    middle.enforce_glossary(bilingual_mj, glossary, reference_for_cleanup)
    middle.tidy_punctuation(bilingual_mj)
    if stitch_enabled:
        stitch_continuations(bilingual_mj, translations, bilingual=True, sources=source_texts)
    middle.relocate_main_captions(bilingual_mj)
    bi_renderer, _ = middle.prepare_image_layout(
        bilingual_mj, reference=middle.load_middle(middle_path), enabled=grid_enabled)
    bilingual_md = middle.render_markdown(bilingual_mj, image_renderer=bi_renderer)
    bilingual_md, _ = middle.replace_terms_in_text(bilingual_md, md_replacements)
    bilingual_md = github_safe_math(bilingual_md)

    (workdir / "zh.md").write_text(zh_md, encoding="utf-8")

    if getattr(args, "docx", False):
        export_docx(zh_mj, workdir, ZH_DOCX, source_dir)
        export_docx(bilingual_mj, workdir, BI_DOCX, source_dir)

    (workdir / "bilingual.md").write_text(bilingual_md, encoding="utf-8")

    images_src = source_dir / "images"
    images_dst = workdir / "images"
    if images_src.is_dir():
        if images_dst.exists():
            shutil.rmtree(images_dst)
        shutil.copytree(images_src, images_dst)

    if not getattr(args, "docx", False):
        md_file = workdir / "zh.md"
        stale = [name for name in (ZH_DOCX, BI_DOCX, "zh.docx", "bilingual.docx")
                 if (workdir / name).is_file()
                 and (workdir / name).stat().st_mtime < md_file.stat().st_mtime]
        if stale:
            paths.say(f"[合并] 注意：{'、'.join(stale)} 比 md 旧；要同步请重跑 finish --docx")
    refresh_readme(workdir)

    if merged_zh:
        paths.say(f"[合并] 跨块断句：接回 {merged_zh} 处（PAPER2ZH_STITCH=0 可关）")
    if grid_stats.get("groups"):
        paths.say(f"[合并] 多子图排版：{grid_stats['groups']} 组 / {grid_stats['cells']} 张子图已按原版面合成表格")
    paths.say(f"[合并] 中文正文：{len(zh_md)} 字符")
    paths.say(f"[合并] 中英对照：{len(bilingual_md)} 字符")
    paths.say(f"[合并] 产物目录：{workdir}")
    return paths.EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))


