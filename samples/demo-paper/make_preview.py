#!/usr/bin/env python3
"""Render the two zh.md outputs to standalone HTML for screenshots.

LaTeX is converted to MathML locally (latex2mathml), so the preview works
without a CDN. Images use a <base> pointing at the original workdir.
"""
from __future__ import annotations

import base64
import html
import re
from pathlib import Path

from latex2mathml.converter import convert as latex_to_mathml
from markdown_it import MarkdownIt

HERE = Path(__file__).resolve().parent
CACHE = HERE.parent.parent / ".cache"
CACHE.mkdir(exist_ok=True)

CSS = """
@page { size: 900px 1500px; margin: 0; }
* { box-sizing: border-box; }
body { margin: 0; background: #f4f6f9; color: #17202a;
       font: 16px/1.8 "Microsoft YaHei", "Segoe UI", sans-serif; }
.page { width: 900px; padding: 42px 54px 60px; background: #fff; margin: 0 auto; }
.badge { display: inline-block; margin-bottom: 18px; padding: 5px 12px;
         border-radius: 999px; background: #e6fffb; color: #0f766e;
         font-size: 13px; font-weight: 700; letter-spacing: .02em; }
h1 { font-size: 27px; line-height: 1.35; margin: 0 0 18px; }
h2 { font-size: 20px; margin: 30px 0 10px; padding-top: 10px;
     border-top: 1px solid #e6eaf0; }
h3 { font-size: 17px; margin: 20px 0 8px; }
p { margin: 0 0 14px; text-align: justify; }
img { width: 100% !important; max-width: 100%; height: auto; display: block;
      margin: 20px auto 8px; border: 1px solid #e2e8f0; }
table { width: 100%; border-collapse: collapse; margin: 14px 0 20px; font-size: 14px; }
th, td { border: 1px solid #dbe2ea; padding: 8px 10px; text-align: left; }
th { background: #f1f5f9; }
.math-block { overflow-x: auto; margin: 20px 0 22px; padding: 14px 18px;
              background: #f8fafc; border-left: 4px solid #5eead4; }
.math-block math { font-size: 21px; }
math { font-family: "Cambria Math", "Latin Modern Math", serif; }
.eqno { float: right; color: #64748b; font-size: 13px; }
"""

MATH_BLOCK = re.compile(r"\$\$(.+?)\$\$", re.S)
MATH_INLINE = re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", re.S)
TAG = re.compile(r"\\tag\{([^}]+)\}")


def mathml(latex: str) -> tuple[str, str]:
    number = ""
    match = TAG.search(latex)
    if match:
        number = match.group(1)
        latex = TAG.sub("", latex).strip()
    try:
        return latex_to_mathml(latex, display="block"), number
    except Exception:
        return "<code>" + html.escape(latex) + "</code>", number


def render(md_path: Path, workdir: Path, badge: str) -> Path:
    text = md_path.read_text(encoding="utf-8")

    def block_repl(match):
        body, number = mathml(match.group(1).strip())
        suffix = f'<span class="eqno">({html.escape(number)})</span>' if number else ""
        return f'<div class="math-block">{suffix}{body}</div>'

    text = MATH_BLOCK.sub(block_repl, text)

    def inline_repl(match):
        try:
            return latex_to_mathml(match.group(1).strip(), display="inline")
        except Exception:
            return "$" + match.group(1) + "$"

    text = MATH_INLINE.sub(inline_repl, text)

    parser = MarkdownIt("commonmark", {"html": True}).enable("table")
    body = parser.render(text)
    base = workdir.resolve().as_uri() + "/"
    page = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<base href="{base}">
<title>{html.escape(badge)} · paper2zh demo</title>
<style>{CSS}</style></head>
<body><div class="page"><div class="badge">{html.escape(badge)}</div>
{body}
</div></body></html>
"""
    out = CACHE / f"demo-preview-{badge.split()[0]}.html"
    out.write_text(page, encoding="utf-8")
    return out


if __name__ == "__main__":
    online = HERE / "翻译结果" / "demo-paper"
    local = HERE / "翻译结果" / "demo-paper-local"
    print(render(online / "bilingual.md", online, "在线模型 · DeepSeek V4.1 Flash · 双语"))
    print(render(local / "bilingual.md", local, "本地 9B · Qwen3.5-9B Q4 · 双语"))
