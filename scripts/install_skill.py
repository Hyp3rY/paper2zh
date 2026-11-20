#!/usr/bin/env python3
"""把 skill 装进各 AI 工具的目录，并登记安装位置。

由「1-开始安装.cmd」自动调用。做四件事：
  1. 装进通用目录 %USERPROFILE%\\.agents\\skills\\paper2zh
     （DSH / Codex / Cursor / Gemini CLI 读这一个位置）
  2. 装进 %USERPROFILE%\\.claude\\skills\\paper2zh（Claude Code 读）
  3. 检测到 WorkBuddy（国服/国际服）与豆包时，装进它们各自的 skills 目录
  4. 把包根目录写进 %USERPROFILE%\\.paper2zh\\config.json

各工具的目录来自实机探测（2026-11），不是猜的：
  WorkBuddy 国服    ~/.workbuddy/skills        （域名 workbuddy.cn / codebuddy.cn）
  WorkBuddy 国际服  ~/.workbuddy-ai/skills     （域名 codebuddy.ai）
  豆包             <User Data>/Default/.doubao/agent_mode/workspace/.user_skills
                   以及 ~/Doubao/skills（旧位置，存在就一起放）
只有检测到工具目录才会写，不会凭空造目录。

有些客户端只认「上传技能包」，所以另外支持 --pack 生成可直接导入的 zip。

用法：
    python install_skill.py [--dry-run] [--list] [--pack 输出.zip]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# 嵌入式 Python 的 ._pth 会关掉「脚本目录自动进 sys.path」，这里显式登记一次。
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import paths  # noqa: E402

SKILL_FILES = ["SKILL.md", "scripts", "references"]

HOME = Path.home()
LOCAL_APP_DATA = Path(os.environ.get("LOCALAPPDATA") or HOME / "AppData" / "Local")
DOUBAO_DATA = LOCAL_APP_DATA / "Doubao" / "User Data" / "Default" / ".doubao" / "agent_mode" / "workspace"


def skill_root(root: Path) -> Path:
    """发行态是 <root>\\skill\\paper2zh，开发态是仓库根目录。"""
    bundled = root / "skill" / "paper2zh"
    return bundled if bundled.is_dir() else root


def detect_targets() -> list[dict]:
    """返回所有候选目标；*marker* 存在才认为该工具装了。"""
    candidates = [
        {
            "label": "通用目录（DSH / Codex / Cursor / Gemini CLI）",
            "dir": HOME / ".agents" / "skills" / "paper2zh",
            "marker": HOME / ".agents",
            "always": True,
        },
        {
            "label": "Claude Code",
            "dir": HOME / ".claude" / "skills" / "paper2zh",
            "marker": HOME / ".claude",
            "always": True,
        },
        {
            "label": "WorkBuddy 国服",
            "dir": HOME / ".workbuddy" / "skills" / "paper2zh",
            "marker": HOME / ".workbuddy",
        },
        {
            "label": "WorkBuddy 国际服",
            "dir": HOME / ".workbuddy-ai" / "skills" / "paper2zh",
            "marker": HOME / ".workbuddy-ai",
        },
        {
            "label": "豆包（用户技能目录）",
            "dir": DOUBAO_DATA / ".user_skills" / "paper2zh",
            "marker": DOUBAO_DATA,
        },
        {
            "label": "豆包（旧版 skills 目录）",
            "dir": HOME / "Doubao" / "skills" / "paper2zh",
            "marker": HOME / "Doubao" / "skills",
        },
    ]
    for item in candidates:
        item["detected"] = bool(item.get("always")) or item["marker"].exists()
    return candidates


def _replace_tree(origin: Path, destination: Path) -> None:
    """替换目录；被占用（客户端正在读）时先改名再复制。"""
    try:
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(origin, destination,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        return
    except PermissionError:
        pass
    backup = destination.with_name(destination.name + ".old")
    try:
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        if destination.exists():
            destination.rename(backup)
        shutil.copytree(origin, destination,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    except PermissionError as exc:
        raise PermissionError(
            f"{destination} 被占用（多半是 AI 客户端正在读取）；关掉客户端后重试"
        ) from exc
    shutil.rmtree(backup, ignore_errors=True)


def copy_skill(source: Path, target: Path, dry_run: bool) -> int:
    if dry_run:
        return 0
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for name in SKILL_FILES:
        origin = source / name
        if not origin.exists():
            continue
        destination = target / name
        if origin.is_dir():
            _replace_tree(origin, destination)
            copied += sum(1 for _ in destination.rglob("*") if _.is_file())
        else:
            shutil.copy2(origin, destination)
            copied += 1
    return copied


def pack_skill(source: Path, out: Path) -> int:
    """打成一个能上传的 zip（压缩包内根目录就是 paper2zh/SKILL.md）。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in SKILL_FILES:
            origin = source / name
            if origin.is_dir():
                for file in origin.rglob("*"):
                    if file.is_file() and "__pycache__" not in file.parts:
                        archive.write(file, f"paper2zh/{file.relative_to(source)}")
                        count += 1
            elif origin.is_file():
                archive.write(origin, f"paper2zh/{origin.name}")
                count += 1
    return count


def write_config(root: Path, installed: list[str], dry_run: bool) -> Path:
    config_path = paths.user_config_path()
    if dry_run:
        return config_path
    config = {
        "root": str(root),
        # 两种安装形态脚本位置不同（<root>\scripts 或 <root>\skill\paper2zh\scripts），
        # 直接登记实际目录，agent 不必猜。
        "scripts": str(paths.scripts_dir()),
        "python": str(paths.python_exe(root)),
        "bundle_version": "1.2.2",
        "installed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "skill_targets": installed,
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if config_path.is_file():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
    existing.update(config)
    text = json.dumps(existing, ensure_ascii=False, indent=1)
    for _ in range(3):
        try:
            config_path.write_text(text, encoding="utf-8")
            return config_path
        except PermissionError:
            time.sleep(0.4)
    # 仍然被占用：把旧文件改名后再写一份
    backup = config_path.with_name(config_path.name + ".old")
    try:
        if backup.exists():
            backup.unlink()
        config_path.rename(backup)
    except OSError:
        pass
    try:
        config_path.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"  [注意] 安装位置写不进配置：{exc}")
        print("         技能副本已装好，但下次运行可能找不到安装目录；关掉占用它的程序后重跑。")
    return config_path


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="把 PDF 转中文 skill 装进各 AI 工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="只显示会装到哪，不写文件")
    parser.add_argument("--list", action="store_true", help="只列出检测结果")
    parser.add_argument("--pack", metavar="ZIP", help="另外打一个可上传的技能包 zip")
    args = parser.parse_args(argv[1:])

    paths.setup_stdout()
    print("=" * 44)
    print("  PDF 转中文 - 装进 AI 工具")
    print("=" * 44)

    root = paths.find_root()
    source = skill_root(root)
    if not (source / "SKILL.md").is_file():
        paths.fail(paths.EXIT_NO_BUNDLE, f"找不到 skill 文件（{source / 'SKILL.md'}）",
                   "重新解压安装包；或先跑一次「1-开始安装.cmd」。")

    print(f"\n安装位置：{root}")
    print(f"skill 来源：{source}\n")

    targets = detect_targets()
    if args.list:
        for item in targets:
            mark = "已检测到" if item["detected"] else "未安装"
            print(f"  [{mark}] {item['label']}\n            {item['dir']}")
        return paths.EXIT_OK

    installed: list[str] = []
    failures: list[tuple[str, str, str]] = []
    for item in targets:
        if not item["detected"]:
            print(f"  [跳过]   {item['label']}（没检测到）")
            continue
        try:
            count = copy_skill(source, item["dir"], args.dry_run)
        except OSError as exc:
            failures.append((item["label"], str(item["dir"]), exc.strerror or str(exc)))
            print(f"  [注意]   {item['label']} 没装上：{exc.strerror or exc}")
            continue
        installed.append(str(item["dir"]))
        action = "将安装到" if args.dry_run else "已装进"
        print(f"  [{action}] {item['label']}\n            {item['dir']}（{count} 个文件）")

    if args.pack:
        if args.dry_run:
            print(f"\n（演练模式）会生成技能包：{args.pack}")
        else:
            count = pack_skill(source, Path(args.pack).expanduser())
            print(f"\n  技能包已生成：{args.pack}（{count} 个文件，可在客户端里「上传技能」导入）")

    config_path = write_config(root, installed, args.dry_run)
    if args.dry_run:
        print(f"\n（演练模式）会写入配置文件：{config_path}")
        return paths.EXIT_OK

    print(f"\n  安装位置已登记：{config_path}")
    print("\n完成。现在可以对 AI 说：「把 D:\\论文\\xxx.pdf 翻成中文」。")
    print("如果某个客户端里看不到 paper2zh，重启客户端；仍看不到就用 --pack 生成技能包手动导入。")
    if failures:
        print("\n以下客户端这次没装上（其它已装好）：")
        for label, path, reason in failures:
            print(f"  - {label}：{path}\n    {reason}")
        print("  多半是客户端正在读取该目录：关掉对应客户端后重跑「1-开始安装.cmd」。")
        return paths.EXIT_DISK_OR_PERM
    return paths.EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))
