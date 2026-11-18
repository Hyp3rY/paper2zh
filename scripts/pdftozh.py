#!/usr/bin/env python3
"""主入口：准备 → 翻译 → 合并 → 质检。

子命令：
    prepare <pdf>        解析 PDF、切块（会按硬件自动选档位）
    next                 取出还没翻的段落（给 agent 用）
    apply <file>         提交译文
    finish               回填 + 渲染 + 质检，出 zh.md / bilingual.md / qa.json
    status [目录]        一行状态，不阻塞、不启动新任务
    doctor               自检（等同「1-开始安装.cmd」的自检）
    version              版本信息

设计原则（提示词第 11 节）：解析失败就报错，不许偷偷降档；产物只写进「翻译结果/」。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# 嵌入式 Python 的 ._pth 会关掉「脚本目录自动进 sys.path」，这里显式登记一次。
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
import paths

STAGES = ["准备", "解析", "分块", "翻译", "合并", "质检"]
PROGRESS_FILE = ".progress.json"
# 只认「按页推进」的条；OCR/表格那些 tqdm 的计数不是页数
PAGE_PROGRESS_RE = re.compile(r"Layout Predict:\s+\d+%\|[^|]*\|\s*(\d+)/(\d+)")


# ---------------------------------------------------------------- 进度


class Progress:
    """写 .progress.json + 每 10 秒至少一行心跳（提示词 4.10）。"""

    def __init__(self, workdir: Path, stage: str, total: int = 0):
        self.workdir = workdir
        self.stage = stage
        self.stage_name = stage
        self.total = total
        self.current = 0
        self.started = time.time()
        self.last_line_at = 0.0
        self.last_beat = 0.0
        self.file = workdir / PROGRESS_FILE
        self._write()

    def _text(self) -> str:
        elapsed = int(time.time() - self.started)
        if self.total:
            pct = min(100, int(self.current * 100 / self.total))
            eta = int(elapsed / max(self.current, 1) * max(self.total - self.current, 0))
            return (f"[{self.stage_name}] {self.current}/{self.total} 页，"
                    f"已用 {elapsed // 60}:{elapsed % 60:02d}，"
                    f"预计还需 {eta // 60}:{eta % 60:02d}")
        return f"[{self.stage_name}] 进行中，已用 {elapsed // 60}:{elapsed % 60:02d}"

    def _write(self) -> None:
        elapsed = int(time.time() - self.started)
        pct = min(100, int(self.current * 100 / self.total)) if self.total else 0
        eta = (int(elapsed / max(self.current, 1) * max(self.total - self.current, 0))
               if self.total else None)
        payload = {
            "stage": self.stage,
            "stage_name": self.stage_name,
            "current": self.current,
            "total": self.total,
            "pct": pct,
            "elapsed_sec": elapsed,
            "eta_sec": eta,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_line": self._text(),
        }
        try:
            self.file.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def say(self, line: str, *, force: bool = False) -> None:
        now = time.time()
        if force or now - self.last_beat >= 10:
            self.last_beat = now
            print(line, flush=True)
            self.last_line_at = now
            self._write()

    def set_stage(self, stage: str, total: int = 0) -> None:
        self.stage = stage
        self.stage_name = stage
        self.total = total
        self.current = 0
        self.started = time.time()
        self.say(self._text(), force=True)

    def advance(self, current: int, total: int | None = None) -> None:
        self.current = current
        if total:
            self.total = total
        self.say(self._text())

    def finish_stage(self, message: str) -> None:
        self.current = self.total or self.current
        print(message, flush=True)
        self._write()


# ---------------------------------------------------------------- 解析


def hardware_tier(root: Path) -> str:
    """按 hardware detect 选档：有独显 → standard，否则 basic。"""
    env = paths.runtime_env(root)
    cmd = [str(paths.python_exe(root)), "-m", "mineru.kit.main", "hardware", "detect", "--json"]
    try:
        result = subprocess.run(cmd, env=env, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        return "basic"
    text = result.stdout or ""
    start = text.find("{")
    if start < 0:
        return "basic"
    try:
        payload = json.loads(text[start:])
    except json.JSONDecodeError:
        return "basic"
    device = str(payload.get("device", ""))
    return "standard" if "gpu" in device.lower() else "basic"


def run_parse(root: Path, pdf: Path, out_dir: Path, tier: str, pages: str | None,
              progress: Progress) -> None:
    """跑一次 MinerU；失败自动重试一次，仍失败就把它的原话带出来。"""
    env = paths.runtime_env(root)
    cmd = [
        str(paths.python_exe(root)), "-m", "mineru.kit.main", "parse",
        str(pdf), "-o", str(out_dir), "-f", "zip", "--tier", tier,
    ]
    if pages:
        cmd += ["--pages", pages]

    tail: list[str] = []
    for attempt in (1, 2):
        process = subprocess.Popen(
            cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        assert process.stdout is not None
        total_pages = 0
        tail = []
        for line in process.stdout:
            clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line).rstrip()
            if clean:
                tail.append(clean)
                tail = tail[-12:]
            match = PAGE_PROGRESS_RE.search(clean)
            if match:
                current, total = int(match.group(1)), int(match.group(2))
                if total > 1 and total != total_pages:
                    total_pages = total
                    progress.total = total
                progress.advance(current, total_pages or None)
            # 静默超过 10 秒必须说句话，不能让人以为卡死
            if time.time() - progress.last_beat > 10:
                progress.say(progress._text())
        code = process.wait()
        if code == 0:
            return
        if attempt == 1:
            progress.say("[解析] 第一次失败，自动重试一次…", force=True)
            time.sleep(2)

    detail = next((line for line in reversed(tail) if "错误" in line or "Error" in line), None)
    detail = detail or (tail[-1] if tail else "没有输出")
    paths.fail(
        paths.EXIT_PARSE_FAILED,
        f"MinerU 解析失败：{detail[:300]}",
        "先跑「1-开始安装.cmd」确认模型完整；如果是中文路径下的怪问题，"
        "把 PDF 复制到纯英文路径再试；仍失败请把这段报错发给维护者。",
    )


def prepare(args) -> int:
    root = paths.find_root()
    pdf = Path(paths.clean_path(args.pdf)).expanduser().resolve()
    if not pdf.is_file():
        paths.fail(paths.EXIT_BAD_USAGE, f"找不到 PDF：{pdf}", "确认路径和文件名对不对。")

    free = paths.free_space_gb(pdf.parent)
    if free and free < 5:
        paths.fail(paths.EXIT_DISK_TOO_SMALL, f"磁盘只剩 {free:.1f} GB",
                   "清理一下磁盘，至少留 5 GB 再试。")

    workdir = paths.ensure_dir(paths.workdir_for(pdf))
    paths.set_log_file(workdir / "run.log")
    progress = Progress(workdir, "准备")
    local_model = getattr(args, "local_model", "").strip()
    if local_model:
        config = {"api": "lmstudio",
                  "base_url": getattr(args, "local_base_url", "http://127.0.0.1:1234"),
                  "model": local_model}
        (workdir / "local.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=1), encoding="utf-8")
        progress.say(f"[准备] 本地翻译模型：{local_model} @ {config['base_url']}", force=True)
    progress.say(f"[准备] 开始处理：{pdf.name}", force=True)

    tier = args.tier
    if tier == "auto":
        tier = hardware_tier(root)
    # 安装路径含中文时，推理库（mineru_llama_cpp）会因窄字符 API 加载失败。
    # 处理：把它复制到 %LOCALAPPDATA%\paper2zh\libs 并登记进 ._pth，让子进程优先从
    # 英文目录加载。这一步不降档、不静默，失败会明确报错。
    if tier == "standard" and not paths.is_ascii_path(root):
        note = paths.ensure_ascii_libs(root)
        if note:
            progress.say(f"[准备] {note}", force=True)
    progress.say(f"[准备] 档位：{tier}", force=True)
    progress.set_stage("解析")

    # 解析产物先落到短路径临时目录：MinerU 会写 <out>/<pdf 文件名>.zip，
    # 若直接写进产物目录，长文件名会让「stem 出现两次」并撑爆 260 字符路径。
    parse_dir = paths.parse_tempdir(pdf)
    import shutil
    if parse_dir.exists():
        shutil.rmtree(parse_dir, ignore_errors=True)
    paths.ensure_dir(parse_dir)
    if len(str(parse_dir / (pdf.stem + ".zip"))) > 240:
        paths.fail(paths.EXIT_DISK_OR_PERM,
                   f"文件名太长，临时路径已达 {len(str(parse_dir)) + len(pdf.stem) + 4} 字符。",
                   "把 PDF 改成短一点的文件名（40 字符以内）再试。")
    run_parse(root, pdf, parse_dir, tier, args.pages, progress)

    zips = sorted(parse_dir.glob("*.zip"))
    if not zips:
        paths.fail(paths.EXIT_PARSE_FAILED, "解析没有产出 zip 文件",
                   "重新跑一次；持续失败请把日志发给维护者。")
    source_dir = workdir / "source"
    import shutil
    if source_dir.exists():
        shutil.rmtree(source_dir)
    paths.ensure_dir(source_dir)
    with zipfile.ZipFile(zips[0]) as archive:
        archive.extractall(source_dir)
    shutil.rmtree(parse_dir, ignore_errors=True)

    if not (source_dir / "middle_json.json").is_file():
        paths.fail(paths.EXIT_PARSE_FAILED, "解析产物里没有 middle_json.json",
                   "MinerU 版本可能不对，请确认是 4.2.x。")

    progress.finish_stage(f"[解析] 完成：{pdf.name}")
    progress.set_stage("分块")

    import seg
    units_file = workdir / "chunks.json"
    seg_argv = ["seg", "--middle", str(source_dir / "middle_json.json"),
                "--out", str(units_file)]
    glossary = workdir / "glossary.csv"
    if glossary.is_file():
        seg_argv += ["--glossary", str(glossary)]
    if seg.main(seg_argv) != 0:
        return paths.EXIT_PARSE_FAILED

    progress.finish_stage("[分块] 完成")
    paths.write_progress(workdir, "ready", "就绪", 1, 1,
                         "[准备] 已完成，可以开始翻译（用 next 取段落）")
    if local_model:
        paths.say(f"[准备] 下一步：用 `pdftozh.py local --workdir \"<产物目录>\"` 逐段翻译"
                  f"（本地模型 {local_model} 已记录在 local.json）。")
    else:
        paths.say(f"[准备] 下一步：按 SKILL.md 翻译 {units_file.name}，用 `pdftozh.py next` 取段落。")
    return paths.EXIT_OK


# ---------------------------------------------------------------- 收尾


def finish(args) -> int:
    root = paths.find_root()
    workdir = Path(paths.clean_path(args.workdir)).expanduser().resolve() if args.workdir else None
    if workdir is None:
        paths.fail(paths.EXIT_BAD_USAGE, "需要 --workdir 指定产物目录",
                   "例如：paper2zh.cmd finish --workdir \"D:\\论文\\翻译结果\\attention\"。")
    paths.set_log_file(workdir / "run.log")
    progress = Progress(workdir, "合并")
    progress.say("[合并] 开始回填译文", force=True)

    import merge
    merge_argv = ["merge", "--workdir", str(workdir)]
    if getattr(args, "docx", False):
        merge_argv.append("--docx")
    if merge.main(merge_argv) != 0:
        return paths.EXIT_QA_FAILED

    progress.set_stage("质检")
    import qa
    code = qa.main(["qa", "--workdir", str(workdir)])
    # 质检结果要写进「先读我.txt」，所以这里再刷一次
    merge.refresh_readme(workdir)
    if code == 0:
        progress.finish_stage("[质检] 全部通过")
    else:
        progress.finish_stage("[质检] 有问题，详见 qa.json")
    # 必须放在 finish_stage 之后：否则会被它覆盖回「质检进行中」，
    # 实测有 agent 因此在完成后继续轮询、误判还没好。
    line = "[完成] 质检全部通过" if code == 0 else "[完成] 质检未通过，详见 qa.json"
    paths.write_progress(workdir, "done", "完成", 1, 1, line)
    paths.say(f"[完成] 产物目录：{workdir}")

    # 屏幕输出在部分工具环境里会被解码成乱码，所以给一份 UTF-8 的机器可读摘要
    summary = {"ok": code == 0, "exit_code": code, "workdir": str(workdir),
               "qa_json": str(workdir / "qa.json")}
    qa_file = workdir / "qa.json"
    if qa_file.is_file():
        try:
            payload = json.loads(qa_file.read_text(encoding="utf-8"))
            summary["gates"] = {name: gate.get("ok") for name, gate in payload.get("gates", {}).items()}
            summary["issues"] = payload.get("issues", [])
            summary["notes"] = payload.get("notes", [])
        except (OSError, json.JSONDecodeError):
            pass
    if getattr(args, "out", None):
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        paths.say(f"[完成] 结果已写入 {out}")
    if getattr(args, "json", False):
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    return code


def status(args) -> int:
    workdir = Path(paths.clean_path(args.workdir)).expanduser().resolve()
    file = workdir / PROGRESS_FILE
    if not file.is_file():
        paths.say(f"[状态] 没有找到进度文件（{file}），可能还没开始。")
        return paths.EXIT_OK
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        paths.say("[状态] 进度文件读不出来，可能正在写。")
        return paths.EXIT_OK

    updated = data.get("updated_at")
    stalled = False
    age = 0
    if updated:
        moment = datetime.strptime(updated, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age = int((datetime.now(timezone.utc) - moment).total_seconds())
        stalled = age > 120
    line = data.get("last_line", "")
    stage = data.get("stage", "")
    suffix = f"（{age} 秒前更新）"
    if stage in {"done", "ready"}:
        paths.say(line)          # 终态不掺「进行中」和停滞判断
        return paths.EXIT_OK
    if stalled:
        suffix += " 疑似停滞：可能在加载模型、被杀毒软件扫描、内存不足，或进程已退出"
    paths.say(f"{line}{suffix}")
    return paths.EXIT_OK


def doctor(args) -> int:
    import doctor
    return doctor.main(["doctor"])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pdftozh",
        description="英文论文 PDF → 中文 Markdown（本地解析 + agent 翻译）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "常用：\n"
            "  paper2zh.cmd prepare 论文.pdf  解析并分块\n"
            "  paper2zh.cmd next --limit 10   取 10 段待翻\n"
            "  paper2zh.cmd apply 译文.json   提交译文\n"
            "  paper2zh.cmd finish --workdir … 出 zh.md 并质检\n"
        ),
    )
    sub = parser.add_subparsers(dest="command")

    prepare_parser = sub.add_parser("prepare", help="解析 PDF 并分块")
    prepare_parser.add_argument("pdf")
    prepare_parser.add_argument("--tier", default="auto", choices=["auto", "basic", "standard"])
    prepare_parser.add_argument("--pages", default=None, help="只解析部分页，例如 1-5")
    prepare_parser.add_argument("--local-model", default="",
                                help="记录本地翻译模型（LM Studio 模型 id），之后 local 不用再传")
    prepare_parser.add_argument("--local-base-url", default="http://127.0.0.1:1234",
                                help="LM Studio 地址（默认 1234）")

    next_parser = sub.add_parser("next", help="取出待翻段落")
    next_parser.add_argument("--workdir", required=True)
    next_parser.add_argument("--limit", type=int, default=10)
    next_parser.add_argument("--model", default="")
    next_parser.add_argument("--json", action="store_true")
    next_parser.add_argument("--out", help="把 JSON 写进文件（Windows 终端编码不可靠时用）")

    apply_parser = sub.add_parser("apply", help="提交译文")
    apply_parser.add_argument("--workdir", required=True)
    apply_parser.add_argument("--file", required=True,
                              help="译文 JSON，或包含 <uid>.txt 的目录（免 JSON 转义）")
    apply_parser.add_argument("--json", action="store_true", help="把收卷结果打成 JSON")
    apply_parser.add_argument("--out", help="把收卷结果写成 UTF-8 JSON 文件")

    local_parser = sub.add_parser("local", help="用本地 LM Studio 模型逐段翻译（绕开 agent 上下文）")
    local_parser.add_argument("--workdir", default="")
    local_parser.add_argument("--base-url", default="",
                              help="默认读产物目录的 local.json，再回退 127.0.0.1:1234")
    local_parser.add_argument("--model", default="", help="LM Studio 里的模型 id")
    local_parser.add_argument("--limit", type=int, default=10)
    local_parser.add_argument("--probe", action="store_true",
                              help="只探测本机有没有在跑的本地模型服务")
    local_parser.add_argument("--temperature", type=float, default=0.2)
    local_parser.add_argument("--json", action="store_true")
    local_parser.add_argument("--out", help="把结果写成 UTF-8 JSON 文件")

    finish_parser = sub.add_parser("finish", help="合并、渲染、质检")
    finish_parser.add_argument("--workdir", required=True)
    finish_parser.add_argument("--json", action="store_true", help="把结果打成 JSON")
    finish_parser.add_argument("--out", help="把结果写成 UTF-8 JSON 文件")
    finish_parser.add_argument("--docx", action="store_true", help="另外导出一份 Word（zh.docx）")

    status_parser = sub.add_parser("status", help="查看进度")
    status_parser.add_argument("--workdir", required=True)
    status_parser.add_argument("--json", action="store_true")
    status_parser.add_argument("--model", default="")

    where_parser = sub.add_parser("where", help="打印安装位置、脚本目录与解释器路径")
    where_parser.add_argument("--json", action="store_true")

    export_parser = sub.add_parser("export", help="把已完成的中文/双语版导出成 Word")
    export_parser.add_argument("--workdir", required=True)
    export_parser.add_argument("--json", action="store_true")
    export_parser.add_argument("--out", help="把结果写成 UTF-8 JSON 文件")

    terms_parser = sub.add_parser("terms", help="从 chunks.json 提取候选术语（只读）")
    terms_parser.add_argument("--workdir", required=True)
    terms_parser.add_argument("--top", type=int, default=80)
    terms_parser.add_argument("--out", help="写成 CSV（en,count,pages）")

    sub.add_parser("doctor", help="自检")
    sub.add_parser("version", help="版本信息")

    # 拖动 PDF 到 paper2zh.cmd 上：参数就是一个 .pdf 路径，直接当 prepare
    if len(argv) >= 2 and argv[1].lower().endswith(".pdf"):
        argv = [argv[0], "prepare", *argv[1:]]

    args = parser.parse_args(argv[1:])
    paths.setup_stdout()
    if args.command == "prepare":
        return prepare(args)
    if args.command == "next":
        import translate
        extra = (["--json"] if args.json else []) + (["--out", args.out] if args.out else [])
        return translate.main(["translate", "next", "--workdir", args.workdir,
                               "--limit", str(args.limit), "--model", args.model] + extra)
    if args.command == "apply":
        import translate
        extra = (["--json"] if getattr(args, "json", False) else [])
        extra += (["--out", args.out] if getattr(args, "out", None) else [])
        return translate.main(["translate", "apply", "--workdir", args.workdir,
                               "--file", args.file] + extra)
    if args.command == "local":
        import translate
        if getattr(args, "probe", False):
            return translate.command_probe(args.base_url, args.json)
        if not args.workdir:
            paths.fail(paths.EXIT_BAD_USAGE, "local 需要 --workdir",
                       "加 --workdir \"<产物目录>\"；只探测服务请用 local --probe。")
        return translate.command_local(
            Path(paths.clean_path(args.workdir)).expanduser().resolve(),
            args.base_url, args.model, args.limit, args.temperature,
            json_out=args.json,
            out_path=Path(args.out).expanduser() if args.out else None)
    if args.command == "finish":
        return finish(args)
    if args.command == "status":
        if getattr(args, "json", False):
            import translate
            return translate.main(["translate", "status", "--workdir", args.workdir,
                                   "--model", args.model, "--json"])
        return status(args)
    if args.command == "where":
        root = paths.find_root()
        scripts = paths.scripts_dir()
        layout = "skill" if "skill" in scripts.parts else "source"
        info = {"root": str(root), "scripts": str(scripts), "layout": layout,
                "python": str(paths.python_exe(root)),
                "config": str(paths.user_config_path())}
        if args.json:
            print(json.dumps(info, ensure_ascii=False, indent=1))
        else:
            for key, value in info.items():
                paths.say(f"{key:<9} {value}")
        return paths.EXIT_OK
    if args.command == "export":
        import merge
        code = merge.main(["merge", "--workdir", args.workdir, "--docx"])
        if code != 0:
            return code
        paths.say(f"[导出] 完成：{Path(args.workdir) / 'zh.docx'} 与 bilingual.docx")
        summary = {"ok": True, "workdir": str(args.workdir),
                   "files": [str(Path(args.workdir) / n) for n in ("zh.docx", "bilingual.docx")]}
        if getattr(args, "out", None):
            out = Path(args.out).expanduser()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
        if getattr(args, "json", False):
            print(json.dumps(summary, ensure_ascii=False, indent=1))
        return code
    if args.command == "terms":
        import seg
        argv = ["seg", "--workdir", args.workdir, "--top", str(args.top)]
        if args.out:
            argv += ["--out", args.out]
        return seg.main_terms(argv)
    if args.command == "doctor":
        return doctor(args)
    if args.command == "version":
        paths.say("paper2zh 1.1.0（本项目 1.1.0；MinerU 4.2.x）")
        return paths.EXIT_OK
    parser.print_help()
    return paths.EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))



