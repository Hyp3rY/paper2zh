#!/usr/bin/env python3
"""自检：9 项检查，全部用中文说人话，最后给一句「该怎么办」。

对应「1-开始安装.cmd」的自检环节。检查顺序（提示词 4.8）：
  1 包完整性   2 Python 能否启动   3 依赖是否齐全   4 模型完整性
  5 磁盘空间   6 路径含中文/空格   7 杀软是否删了文件
  8 硬件档位   9 跑一页样例预热

用法：
    python doctor.py [--json] [--quick]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# 嵌入式 Python 的 ._pth 会关掉「脚本目录自动进 sys.path」，这里显式登记一次。
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
import paths

MARK_OK = "通过"
MARK_WARN = "注意"
MARK_FAIL = "失败"


class Report:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self.failed = False
        self.warned = False

    def add(self, name: str, mark: str, detail: str, hint: str = "") -> None:
        self.items.append({"name": name, "mark": mark, "detail": detail, "hint": hint})
        if mark == MARK_FAIL:
            self.failed = True
        elif mark == MARK_WARN:
            self.warned = True
        print(f"[{mark}] {name}：{detail}", flush=True)
        if hint:
            print(f"        → {hint}", flush=True)


def check_bundle(root: Path, report: Report) -> None:
    # 发行态脚本在 skill\paper2zh\scripts，开发态在 scripts
    scripts_ok = (root / "scripts").is_dir() or (root / "skill" / "paper2zh" / "scripts").is_dir()
    missing = [name for name in ("runtime", "MANIFEST.json") if not (root / name).exists()]
    if not scripts_ok:
        missing.append("scripts")
    if missing:
        report.add("包完整性", MARK_FAIL, f"缺少 {', '.join(missing)}",
                   "重新解压一份安装包，把整个文件夹替换掉。")
        return
    manifest = {}
    try:
        manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        report.add("包完整性", MARK_WARN, "MANIFEST.json 读不出来",
                   "不影响使用；如果后续报版本错误，重新解压安装包。")
        return
    report.add("包完整性", MARK_OK,
               f"版本 {manifest.get('bundle_version', '未知')}，MinerU {manifest.get('mineru', '未知')}")


def check_skill_copies(root: Path, report: Report) -> None:
    """包里的 skill 与装进 agent 目录的那份是否一致。

    实测踩过：两份代码会漂移（bundle 有重试与报错原文，agent 目录还是旧版），
    排障时 grep 错了副本，行为与代码完全对不上。
    """
    bundled = root / "skill" / "paper2zh" / "SKILL.md"
    if not bundled.is_file():
        bundled = root / "SKILL.md"
    if not bundled.is_file():
        return
    import hashlib

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:12]

    base = digest(bundled)
    copies = [
        Path.home() / ".agents" / "skills" / "paper2zh" / "SKILL.md",
        Path.home() / ".claude" / "skills" / "paper2zh" / "SKILL.md",
        Path.home() / ".workbuddy" / "skills" / "paper2zh" / "SKILL.md",
        Path.home() / ".workbuddy-ai" / "skills" / "paper2zh" / "SKILL.md",
    ]
    stale = [str(path.parent.parent) for path in copies if path.is_file() and digest(path) != base]
    if stale:
        report.add("skill 副本", MARK_WARN, f"{len(stale)} 处 agent 目录里是旧版",
                   "重新双击「1-开始安装.cmd」同步；排障时以包内 skill 为准。")
    else:
        report.add("skill 副本", MARK_OK, "与包内版本一致")


def check_python(root: Path, report: Report) -> None:
    try:
        result = subprocess.run(
            [str(paths.python_exe(root)), "-c", "import sys; print(sys.version.split()[0])"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        report.add("Python 启动", MARK_FAIL, f"起不来：{exc}",
                   "多半是杀毒软件删了文件，重新解压安装包。")
        return
    if result.returncode != 0:
        report.add("Python 启动", MARK_FAIL, result.stderr.strip()[:200],
                   "重新解压安装包；还不行把这段信息发给维护者。")
        return
    report.add("Python 启动", MARK_OK, f"自带 Python {result.stdout.strip()}")


def check_deps(root: Path, report: Report) -> None:
    marker = root / "runtime" / ".deps-ok"
    try:
        result = subprocess.run(
            [str(paths.python_exe(root)), "-c",
             "import mineru, sys; print(mineru.__name__)"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
            env=paths.runtime_env(root),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        report.add("依赖齐全", MARK_FAIL, f"导入 mineru 失败：{exc}",
                   "双击「1-开始安装.cmd」重装依赖。")
        return
    if result.returncode != 0:
        report.add("依赖齐全", MARK_FAIL, "导入 mineru 失败",
                   "双击「1-开始安装.cmd」，它会补齐缺的依赖。")
        return
    if not marker.is_file():
        report.add("依赖齐全", MARK_WARN, "能导入 mineru，但缺少完成标记",
                   "用「1-开始安装.cmd」跑一次更稳妥。")
        return
    report.add("依赖齐全", MARK_OK, f"mineru 可导入，标记 {marker.read_text(encoding='utf-8').strip()[:16]}…")


def check_models(root: Path, report: Report) -> None:
    models = paths.models_dir(root)
    if not models.is_dir() or not any(models.iterdir()):
        report.add("模型完整性", MARK_FAIL, "模型目录是空的",
                   "双击「1-开始安装.cmd」下载模型（约 2 GB）。")
        return
    try:
        result = subprocess.run(
            [str(paths.python_exe(root)), "-m", "mineru.kit.main", "models", "verify",
             "--tier", "standard"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, env=paths.runtime_env(root),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        report.add("模型完整性", MARK_FAIL, f"校验没跑起来：{exc}",
                   "双击「1-开始安装.cmd」修复模型。")
        return
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["未知错误"]
        report.add("模型完整性", MARK_FAIL, detail[0][:160],
                   "双击「1-开始安装.cmd」，它会只补下缺的那部分。")
        return
    report.add("模型完整性", MARK_OK, "standard 档模型校验正常")


def check_disk(root: Path, report: Report) -> None:
    free = paths.free_space_gb(root)
    if free < 5:
        report.add("磁盘空间", MARK_FAIL, f"只剩 {free:.1f} GB",
                   "清理磁盘，至少留 5 GB。")
    elif free < 10:
        report.add("磁盘空间", MARK_WARN, f"剩 {free:.1f} GB，偏紧",
                   "建议再腾出几 GB，解析大论文要临时空间。")
    else:
        report.add("磁盘空间", MARK_OK, f"可用 {free:.0f} GB")


def check_path(root: Path, report: Report) -> None:
    text = str(root)
    has_cn = any("\u4e00" <= ch <= "\u9fff" for ch in text)
    has_space = " " in text
    if has_cn or has_space:
        problems = [name for name, flag in (("中文", has_cn), ("空格", has_space)) if flag]
        try:
            note = paths.ensure_ascii_libs(root)
        except SystemExit:
            note = ""
        if note:
            report.add("安装路径", MARK_WARN, f"路径里有{'和'.join(problems)}（已自动兼容）",
                       "推理库已复制到纯英文目录，standard 档可用；换机器后重跑一次自检即可。")
        else:
            report.add("安装路径", MARK_WARN, f"路径里有{'和'.join(problems)}",
                       "建议把整个文件夹挪到纯英文路径（例如 D:\\Tools\\paper2zh）。")
    else:
        report.add("安装路径", MARK_OK, "纯英文、无空格")


def check_antivirus(root: Path, report: Report) -> None:
    key_files = [
        root / "runtime" / "python" / "python.exe",
        root / "runtime" / "python" / "python312.dll",
        root / "runtime" / "site-packages" / "mineru_llama_cpp" / "bin" / "llama.dll",
    ]
    missing = [item.name for item in key_files if not item.is_file()]
    if missing:
        report.add("关键文件", MARK_FAIL, f"少了 {', '.join(missing)}",
                   "大概率被杀毒软件隔离了：把它从隔离区恢复，或重新解压安装包并把目录加入白名单。")
    else:
        report.add("关键文件", MARK_OK, "运行时与推理库都在")


def check_hardware(root: Path, report: Report) -> None:
    try:
        result = subprocess.run(
            [str(paths.python_exe(root)), "-m", "mineru.kit.main", "hardware", "detect", "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180, env=paths.runtime_env(root),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        report.add("硬件档位", MARK_WARN, f"检测失败：{exc}", "先用默认档位试试，不影响基本使用。")
        return
    text = result.stdout or ""
    start = text.find("{")
    if start < 0:
        report.add("硬件档位", MARK_WARN, "拿不到检测结果", "先用默认档位。")
        return
    try:
        data = json.loads(text[start:])
    except json.JSONDecodeError:
        report.add("硬件档位", MARK_WARN, "检测结果看不懂", "先用默认档位。")
        return
    device = str(data.get("device", ""))
    gpu = "gpu" in device.lower()
    tier = "standard" if gpu else "basic"
    detail = ("有独显（" + device + "），推荐 standard 档，解析更快"
              if gpu else "没检测到可用独显，推荐 basic 档（快很多）")
    report.add("硬件档位", MARK_OK, detail)
    (root / "home").mkdir(parents=True, exist_ok=True)
    (root / "home" / "tier.txt").write_text(tier, encoding="utf-8")


def check_warmup(root: Path, report: Report, quick: bool) -> None:
    sample = root / "samples" / "one-page.pdf"
    if quick:
        report.add("样例预热", MARK_WARN, "已跳过（--quick）", "首次正式使用前建议跑一次完整自检。")
        return
    if not sample.is_file():
        report.add("样例预热", MARK_WARN, "包里没有 samples\\one-page.pdf",
                   "跳过预热；第一次正式解析会慢几分钟，这是正常的预热，不是卡死。")
        return
    try:
        result = subprocess.run(
            [str(paths.python_exe(root)), "-m", "mineru.kit.main", "parse",
             str(sample), "-o", str(root / "home" / "warmup"), "-f", "markdown",
             "--pages", "1", "--tier", "basic"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, env=paths.runtime_env(root),
        )
    except subprocess.TimeoutExpired:
        report.add("样例预热", MARK_FAIL, "一页样例超过 15 分钟没跑完",
                   "可能内存不足或模型没装好；先看「模型完整性」那一项。")
        return
    if result.returncode != 0:
        report.add("样例预热", MARK_FAIL, "一页样例解析失败",
                   "先看上一项模型检查；仍失败请联系维护者。")
        return
    (root / "home" / ".warmed").write_text("ok", encoding="utf-8")
    report.add("样例预热", MARK_OK, "一页样例跑通，模型已预热")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="PDF 转中文 自检")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    parser.add_argument("--quick", action="store_true", help="跳过样例预热")
    args = parser.parse_args(argv[1:])

    paths.setup_stdout()
    print("=" * 44)
    print("  PDF 转中文 - 自检")
    print("  按顺序检查 9 项，只报结论和该怎么办")
    print("=" * 44)

    root = paths.find_root()
    print(f"[信息] 安装位置：{root}\n", flush=True)

    report = Report()
    check_bundle(root, report)
    check_skill_copies(root, report)
    check_python(root, report)
    check_deps(root, report)
    check_models(root, report)
    check_disk(root, report)
    check_path(root, report)
    check_antivirus(root, report)
    check_hardware(root, report)
    check_warmup(root, report, args.quick)

    print("=" * 44)
    if report.failed:
        print("结论：有项目没通过，先按上面「→」的提示处理。")
        code = paths.EXIT_DEPS_FAILED
    elif report.warned:
        print("结论：可以正常使用，但有需要注意的地方。")
        code = paths.EXIT_OK
    else:
        print("结论：全部正常，可以开始翻译了。")
        code = paths.EXIT_OK

    if args.json:
        print(json.dumps({"root": str(root), "ok": not report.failed,
                          "items": report.items}, ensure_ascii=False, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv))




