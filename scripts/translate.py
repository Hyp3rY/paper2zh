#!/usr/bin/env python3
"""翻译单元的管理与校验（不联网，翻译由 agent 完成）。

设计：翻译这件事由调用方（agent 自己的模型）做，脚本只负责三件事：
  1. 发卷：把还没翻的段落连同术语表吐出来（`next`）
  2. 收卷：校验译文并把结果按「原文 hash + 术语表 hash + 模型名」缓存（`apply`）
  3. 对账：随时能查还剩多少（`status`）

校验不通过的字句一律不收，并逐条说明原因（公式对不上 / 没中文 / 空译）。

用法：
    python translate.py next   --workdir <产物目录> [--limit 10] [--json]
    python translate.py apply  --workdir <产物目录> --file 译文.json
    python translate.py status --workdir <产物目录> [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# 嵌入式 Python 的 ._pth 会关掉「脚本目录自动进 sys.path」，这里显式登记一次。
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
import middle
import paths

TRANSLATIONS_FILE = "translations.jsonl"
CHUNKS_FILE = "chunks.json"

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# ISSN/ISBN/DOI 这类「标识符 + 编号」本来就没有可译的词，原样保留应放行
IDENTIFIER_RE = re.compile(r"^(?:ISSN(?:-L)?|ISBN|DOI|ORCID|PMID|PMC|arXiv|NCT|ISO|IEC)\b", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


# ---------------------------------------------------------------- 读写


def load_chunks(workdir: Path) -> dict:
    path = workdir / CHUNKS_FILE
    if not path.is_file():
        paths.fail(paths.EXIT_PARSE_FAILED, f"找不到分块文件 {path}",
                   "先运行「准备」这一步（pdftozh.py prepare）。")
    return json.loads(path.read_text(encoding="utf-8"))


def load_translations(workdir: Path) -> dict[str, dict]:
    path = workdir / TRANSLATIONS_FILE
    records: dict[str, dict] = {}
    if not path.is_file():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue  # 断点续译时容忍半行
        if isinstance(record, dict) and record.get("uid"):
            records[record["uid"]] = record
    return records


def append_translations(workdir: Path, records: list[dict]) -> None:
    path = workdir / TRANSLATIONS_FILE
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_glossary(workdir: Path) -> dict[str, str]:
    path = workdir / "glossary.csv"
    if not path.is_file():
        return {}
    import csv

    terms: dict[str, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle):
            if len(row) >= 2 and row[0].strip() and not row[0].lstrip().startswith("#"):
                terms[row[0].strip()] = row[1].strip()
    return terms


def glossary_sha(glossary: dict[str, str]) -> str:
    return middle.sha256_text("\n".join(f"{k},{v}" for k, v in sorted(glossary.items())))


def scoped_glossary(glossary: dict[str, str], source: str) -> dict[str, str]:
    """只取「本段真的用到了」的术语。

    否则改一个术语会把全篇译文缓存作废——实测里 agent 因此不敢动术语表。
    """
    lowered = source.lower()
    return {term: value for term, value in glossary.items() if term.lower() in lowered}


def unit_hash(unit: dict, glossary: dict[str, str], model: str) -> str:
    terms = scoped_glossary(glossary, str(unit.get("source", "")))
    term_hash = middle.sha256_text("\n".join(f"{k},{v}" for k, v in sorted(terms.items())))
    return middle.sha256_text(f"{unit['uid']}\x1f{unit['source']}\x1f{term_hash}\x1f{model}")


# ---------------------------------------------------------------- 模型名

MODEL_FILE = ".model"


def stored_model(workdir: Path) -> str:
    path = workdir / MODEL_FILE
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def resolve_model(workdir: Path, explicit: str = "") -> str:
    """命令行 > .model 文件 > 最后一条译文记录里的模型名 > 空串。

    文档里没写清楚导致过 45 段重复发放：next 用空串、apply 用文件名当模型名，
    两边算出的缓存键永远对不上。最后一条记录的回退也照顾了升级前的旧产物目录。
    """
    if explicit.strip():
        return explicit.strip()
    saved = stored_model(workdir)
    if saved:
        return saved
    tables = load_translations(workdir)
    for record in reversed(list(tables.values())):
        model = str(record.get("model") or "").strip()
        if model:
            return model
    return ""


def remember_model(workdir: Path, model: str) -> None:
    if not model:
        return
    try:
        (workdir / MODEL_FILE).write_text(model, encoding="utf-8")
    except OSError:
        pass


# ---------------------------------------------------------------- 发卷


def flatten_units(chunks: dict) -> list[dict]:
    units: list[dict] = []
    for chunk in chunks.get("chunks", []):
        for unit in chunk.get("units", []):
            unit = dict(unit)
            unit["chunk"] = chunk.get("id")
            units.append(unit)
    return units


def translation_units(workdir: Path) -> list[dict]:
    return flatten_units(load_chunks(workdir))


def record_matches(unit: dict, glossary: dict[str, str], record: dict | None) -> bool:
    """这条译文能否直接复用。

    用「记录自己的模型名」重算哈希，而不是当前命令行/文件的模型名：
    模型名只是译文元数据，换模型不该把已经翻好的段落全部判成没翻
    （实测过：status 显示 11/117、next 把 106 段重发一遍）。
    """
    if not record:
        return False
    return record.get("hash") == unit_hash(unit, glossary, str(record.get("model") or ""))


def pending_units(workdir: Path, model: str) -> tuple[list[dict], dict[str, str], dict[str, dict]]:
    chunks = load_chunks(workdir)
    glossary = read_glossary(workdir)
    done = load_translations(workdir)
    pending: list[dict] = []
    for unit in flatten_units(chunks):
        record = done.get(unit["uid"])
        if record_matches(unit, glossary, record):
            continue
        unit.pop("formulas", None)
        pending.append(unit)
    return pending, glossary, done


def command_next(workdir: Path, limit: int, model: str, as_json: bool, out: Path | None = None) -> int:
    model = resolve_model(workdir, model)
    pending, glossary, done = pending_units(workdir, model)
    batch = pending[:limit]
    units_total = len(translation_units(workdir))
    done_count = units_total - len(pending)
    # 只发「本批真的会用到」的术语：8 批发 8 遍 68 条术语纯属浪费 token
    batch_sources = " ".join(str(unit.get("source", "")) for unit in batch).lower()
    scoped = {term: value for term, value in glossary.items() if term.lower() in batch_sources}
    payload = {
        "model": model,
        "glossary": scoped,
        "glossary_total": len(glossary),
        "instructions": (
            "把每个单位的 source 翻成简体中文，保留正文编号与专有名词；"
            "source 里的 $...$ 是行内公式，必须原样保留、数量与顺序不变；"
            "中英语序不同时，请调整中文语序以匹配原文的公式顺序，不要移动公式位置；"
            "不要翻译 URL/DOI/邮箱/作者名；只输出纯文本，不要 markdown 语法；"
            "结果写成 {\"model\": ..., \"translations\": {\"uid\": \"译文\"}}。"
        ),
        "units": batch,
        "remaining": len(pending),
    }
    paths.write_progress(workdir, "translate", "翻译", done_count, units_total,
                         f"[翻译] 已翻 {done_count}/{units_total} 段，本次发 {len(batch)} 段")
    if as_json or out:
        text = json.dumps(payload, ensure_ascii=False, indent=1)
        if out is not None:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")
            paths.say(f"[翻译] 已写出 {out}")
        else:
            # 交付给 agent 时保证 stdout 只有 JSON（.cmd 的 pause 由 PAPER2ZH_NO_PAUSE 关掉）
            print(text)
        return paths.EXIT_OK

    paths.say(f"[翻译] 共 {len(pending)} 段待翻，本次发 {len(batch)} 段（已完成 {done_count} 段）")
    for unit in batch:
        paths.say(f"\n--- {unit['uid']} ({unit['kind']}, {unit['chars']} 字) ---")
        paths.say(unit["source"])
    if glossary:
        paths.say("\n[术语表] " + "；".join(f"{k}→{v}" for k, v in list(glossary.items())[:20]))
    return paths.EXIT_OK


# ---------------------------------------------------------------- 收卷


def normalise_formula(text: str) -> str:
    return re.sub(r"\s+", "", text)


def lenient_formula(text: str) -> str:
    """去掉公式末尾被 MinerU 吸进来的标点（`=18.6)`、`=5.6.`）。

    这类是解析层的边界问题，不该逼 agent 在译文里复制一个多余的右括号；
    只要去掉尾部标点后两边一致，就认为公式没被改。
    """
    return re.sub(r"[).,;:]+$", "", normalise_formula(text))


def validate_unit(unit: dict, zh: str) -> list[str]:
    problems: list[str] = []
    zh = zh.strip()
    source = str(unit.get("source", ""))
    if not zh:
        problems.append("译文是空的")
        return problems

    # 表格里的纯缩写/符号单元格（CFD、UAM、eVTOL…）允许原样保留
    acronym_cell = (
        unit.get("kind") == "table_cell"
        and len(source) <= 20
        and re.fullmatch(r"[A-Za-z0-9 .\-]+", source.strip()) is not None
    )
    # 子图标号 (a)、单位 (rad/s)、型号 NACA 23015 这类「标签」允许原样交回：
    #   - 整串没有 ≥3 个字母的英文单词；或
    #   - 是 caption / table_cell 且很短、不含两个英文单词组成的短语
    # 这样就不会再逼 agent 把 (a) 硬写成「子图（a）」来讨好校验器。
    label_like = (
        re.search(r"[A-Za-z]{3,}", source) is None
        or (
            unit.get("kind") in {"caption", "table_cell"}
            and len(source) <= 20
            and re.search(r"[A-Za-z]{3,}\s+[A-Za-z]{3,}", source) is None
        )
    )

    expected = [normalise_formula(f) for f in unit.get("formulas", [])]
    found = [normalise_formula(f) for f in middle.split_formulas(zh)]
    if expected != found:
        if [lenient_formula(f) for f in expected] == [lenient_formula(f) for f in found]:
            pass  # 只差末尾标点（解析层问题），放行
        elif len(expected) != len(found):
            problems.append(f"公式数量不对：原文 {len(expected)} 个、译文 {len(found)} 个")
        elif sorted(expected) == sorted(found):
            # 内容都在、只是先后顺序不同：不要让用户以为公式被改了
            problems.append(
                "公式顺序与原文不一致（内容都在）："
                f"原文顺序 {expected}；译文顺序 {found}。"
                "请调整中文语序，让公式按原文顺序出现，不要改动公式本身"
            )
        else:
            for index, (left, right) in enumerate(zip(expected, found)):
                if left == right:
                    continue
                position = next(
                    (i for i, (a, b) in enumerate(zip(left, right)) if a != b),
                    min(len(left), len(right)),
                )
                problems.append(
                    f"第 {index + 1} 个公式内容被改了：原文 {left!r}、译文 {right!r}；"
                    f"第 {position + 1} 个字符起不同"
                )
                break
    identifier_like = bool(IDENTIFIER_RE.match(source.strip()))
    email_like = bool(EMAIL_RE.search(source))
    if not CJK_RE.search(zh) and not (acronym_cell and zh == source.strip()) and not label_like \
            and not (identifier_like and zh == source.strip()) \
            and not (email_like and (zh == source.strip() or EMAIL_RE.search(zh))):
        problems.append("译文里没有任何中文")
    if "```" in zh or zh.startswith("#"):
        problems.append("译文里混进了 markdown 语法")
    return problems


MARKER_RE = re.compile(r"^\s*@@\s*([A-Za-z0-9_#\-]+)\s*$")


def read_marker_pack(path: Path) -> dict[str, str]:
    """读取「批文件」：`@@uid` 一行，下面跟该段纯译文。

    这是批量提交 + 零转义的折中：比 JSON 少一层转义（引号、反斜杠都不用管），
    比一段一 txt 少 N 次工具调用。
    """
    text = path.read_text(encoding="utf-8-sig")
    translations: dict[str, str] = {}
    uid: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        match = MARKER_RE.match(line)
        if match:
            if uid is not None:
                translations[uid] = "\n".join(buffer).strip()
            uid = match.group(1)
            buffer = []
        elif uid is not None:
            buffer.append(line)
    if uid is not None:
        translations[uid] = "\n".join(buffer).strip()
    return translations


def read_translation_dir(directory: Path) -> dict[str, str]:
    """按 `<uid>.txt` 读取译文，彻底绕开 JSON 的 LaTeX 转义。

    手写 JSON 时 `\\mathrm` 会被当成非法转义（`Invalid \\escape`），
    这是实测里最容易卡死流程的一步。一个段落一个 txt 文件就不会有这个问题。
    """
    translations: dict[str, str] = {}
    for file in sorted(directory.glob("*.txt")):
        translations[file.stem] = file.read_text(encoding="utf-8").strip()
    return translations


def command_apply(workdir: Path, file_path: Path, json_out: bool = False,
                  out_path: Path | None = None) -> int:
    paths.set_log_file(workdir / "run.log")
    repaired = False
    if file_path.is_dir():
        translations = read_translation_dir(file_path)
        if not translations:
            paths.fail(paths.EXIT_BAD_USAGE, f"目录里没有 <uid>.txt：{file_path}",
                       "每段译文写成「段号.txt」，文件名就是 next 返回的 uid。")
        payload = {"model": resolve_model(workdir), "translations": translations}
    elif file_path.is_file() and file_path.suffix.lower() == ".json":
        raw = file_path.read_text(encoding="utf-8-sig")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            # 中文译文里出现 ASCII 双引号是最常见的申告失败原因（"升力" → 截断）
            try:
                import json_repair

                payload = json_repair.loads(raw)
                repaired = True
                paths.say("[收卷] 译文 JSON 有语法问题，已自动修复（引号/逗号）；请抽查内容是否完整")
            except Exception:
                paths.fail(
                    paths.EXIT_BAD_USAGE,
                    f"译文文件不是合法 JSON：{exc}",
                    "常见原因：译文里用了英文双引号 \" 或 LaTeX 反斜杠 \\ 没转义。"
                    "建议改用 `@@uid` 批文件或 <uid>.txt 目录提交，完全不用转义。")
    elif file_path.is_file():
        translations = read_marker_pack(file_path)
        if not translations:
            paths.fail(paths.EXIT_BAD_USAGE, f"{file_path.name} 里没有找到 `@@uid` 标记",
                       "批文件格式：一行 `@@p1b6`，下面写这段的译文，再接下一段。")
        payload = {"model": resolve_model(workdir), "translations": translations}
    else:
        paths.fail(paths.EXIT_BAD_USAGE, f"找不到译文文件 {file_path}",
                   "支持：`@@uid` 批文件、<uid>.txt 目录、JSON 文件。")
    model = str(payload.get("model") or "").strip() or resolve_model(workdir) or workdir.name
    remember_model(workdir, model)
    translations = payload.get("translations")
    if not isinstance(translations, dict):
        paths.fail(paths.EXIT_BAD_USAGE, "译文 JSON 缺少 translations 字段",
                   '格式应为 {"model": "…", "translations": {"p0b1": "译文"}}。')

    units = {unit["uid"]: unit for unit in flatten_units(load_chunks(workdir))}
    glossary = read_glossary(workdir)
    ghash = glossary_sha(glossary)

    accepted: list[dict] = []
    rejected: list[tuple[str, str]] = []
    unknown: list[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for uid, zh in translations.items():
        unit = units.get(uid)
        if unit is None:
            unknown.append(uid)
            continue
        problems = validate_unit(unit, str(zh))
        if problems:
            rejected.append((uid, "；".join(problems)))
            continue
        accepted.append(
            {
                "uid": uid,
                "zh": str(zh).strip(),
                "hash": unit_hash(unit, glossary, model),
                "model": model,
                "glossary_sha256": ghash,
                "at": now,
            }
        )

    if accepted:
        append_translations(workdir, accepted)

    paths.say(f"[收卷] 收下 {len(accepted)} 段，退回 {len(rejected)} 段，未知 {len(unknown)} 段")
    for uid, reason in rejected[:10]:
        paths.say(f"  退回 {uid}：{reason}")
    for uid in unknown[:5]:
        paths.say(f"  未知编号 {uid}（不在分块里）")

    pending, _, _ = pending_units(workdir, model)
    paths.say(f"[收卷] 还剩 {len(pending)} 段")
    summary = {
        "ok": not rejected and not unknown,
        "accepted": len(accepted),
        "rejected": [{"uid": uid, "reason": reason} for uid, reason in rejected],
        "unknown": unknown,
        "remaining": len(pending),
        "model": model,
        "repaired": repaired,
    }
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        paths.say(f"[收卷] 结果已写入 {out_path}")
    if json_out:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    return paths.EXIT_OK if not rejected and not unknown else paths.EXIT_QA_FAILED


def command_status(workdir: Path, model: str, as_json: bool) -> int:
    model = resolve_model(workdir, model)
    chunks = load_chunks(workdir)
    units = flatten_units(chunks)
    records = load_translations(workdir)
    glossary = read_glossary(workdir)
    done = sum(1 for unit in units if record_matches(unit, glossary, records.get(unit["uid"])))
    stats = {
        "units": len(units),
        "translated": done,
        "remaining": len(units) - done,
        "pct": round(done * 100 / len(units), 1) if units else 100.0,
        "glossary_terms": len(read_glossary(workdir)),
        "model": model,
        "repaired": repaired,
    }
    if as_json:
        print(json.dumps(stats, ensure_ascii=False))
    else:
        paths.say(
            f"[翻译] 已翻 {done}/{len(units)} 段（{stats['pct']}%），"
            f"术语表 {stats['glossary_terms']} 条"
        )
    return paths.EXIT_OK


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="翻译单元的发卷 / 收卷 / 对账（翻译本身由 agent 完成）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    next_parser = sub.add_parser("next", help="取出还没翻的段落")
    next_parser.add_argument("--workdir", required=True)
    next_parser.add_argument("--limit", type=int, default=10)
    next_parser.add_argument("--model", default="", help="翻译所用模型名（进缓存键）")
    next_parser.add_argument("--json", action="store_true")
    next_parser.add_argument("--out", help="把 JSON 写进文件（Windows 终端编码不可靠时用）")

    apply_parser = sub.add_parser("apply", help="提交译文")
    apply_parser.add_argument("--workdir", required=True)
    apply_parser.add_argument("--file", required=True)
    apply_parser.add_argument("--json", action="store_true")
    apply_parser.add_argument("--out", help="把收卷结果写成 JSON 文件")

    status_parser = sub.add_parser("status", help="查看进度")
    status_parser.add_argument("--workdir", required=True)
    status_parser.add_argument("--model", default="")
    status_parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv[1:])
    paths.setup_stdout()
    workdir = Path(paths.clean_path(args.workdir)).expanduser().resolve()

    if args.command == "next":
        return command_next(workdir, args.limit, args.model, args.json,
                            Path(args.out).expanduser() if args.out else None)
    if args.command == "apply":
        return command_apply(workdir, Path(paths.clean_path(args.file)).expanduser(),
                             json_out=getattr(args, "json", False),
                             out_path=Path(args.out).expanduser() if getattr(args, "out", None) else None)
    return command_status(workdir, args.model, args.json)


if __name__ == "__main__":
    sys.exit(main(sys.argv))



