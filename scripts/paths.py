#!/usr/bin/env python3
"""包定位、运行时路径与统一退出码。

安装路径不固定（提示词 4.9），查找顺序：
    1. 环境变量 PAPER2ZH_HOME
    2. %USERPROFILE%\\.paper2zh\\config.json 的 root
    3. 常见路径兜底（C:\\Tools\\paper2zh、D:\\Tools\\paper2zh）
    4. 都没有 → 退出码 2，提示重新运行「1-开始安装.cmd」

开发态（源码仓库）和发行态（轻包）目录不同，这里统一抹平：
    发行态：<root>\\skill\\paper2zh\\scripts\\*.py、<root>\\runtime\\python\\python.exe
    开发态：<root>\\scripts\\*.py、<root>\\runtime\\python\\python.exe
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------- 退出码

EXIT_OK = 0
EXIT_BAD_USAGE = 1
EXIT_NO_BUNDLE = 2
EXIT_MODEL_MISSING = 3
EXIT_PARSE_FAILED = 4
EXIT_DISK_OR_PERM = 5
EXIT_QA_FAILED = 6
EXIT_VERSION_MISMATCH = 7
EXIT_TRANSLATOR_UNAVAILABLE = 8
EXIT_NETWORK = 9
EXIT_DEPS_FAILED = 10
EXIT_MODEL_DOWNLOAD = 11
EXIT_DISK_TOO_SMALL = 12

EXIT_MEANING = {
    EXIT_NO_BUNDLE: "找不到安装包",
    EXIT_MODEL_MISSING: "模型缺失或损坏",
    EXIT_PARSE_FAILED: "解析失败",
    EXIT_DISK_OR_PERM: "磁盘空间或权限问题",
    EXIT_QA_FAILED: "质检未通过",
    EXIT_VERSION_MISMATCH: "MinerU 版本不符（要求 >=4.2,<5）",
    EXIT_TRANSLATOR_UNAVAILABLE: "翻译环节不可用",
    EXIT_NETWORK: "网络不可达",
    EXIT_DEPS_FAILED: "依赖安装失败",
    EXIT_MODEL_DOWNLOAD: "模型下载失败",
    EXIT_DISK_TOO_SMALL: "磁盘空间不足",
}

MINERU_SPEC = ">=4.2,<5"

CFG_DIR = Path.home() / ".paper2zh"
CFG_FILE = CFG_DIR / "config.json"
FALLBACK_ROOTS = [Path(r"C:\Tools\paper2zh"), Path(r"D:\Tools\paper2zh")]


_LOG_PATH: "Path | None" = None


def set_log_file(path: "Path | None") -> None:
    """把控制台输出同时追加进一个 UTF-8 日志文件。

    PowerShell 抓 .cmd 的 stdout 时按 OEM 代码页解码，中文会变
    「鍑嗗…」。让脚本自己写一份 UTF-8 日志，agent 直接读文件即可。
    """
    global _LOG_PATH
    _LOG_PATH = path
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} run.log =====\n")
        except OSError:
            _LOG_PATH = None


def say(message: str) -> None:
    """统一的中文输出入口，保证 Windows 控制台不乱码。"""
    print(message, flush=True)
    if _LOG_PATH is not None:
        try:
            with _LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(message + "\n")
        except OSError:
            pass


def fail(code: int, message: str, hint: str = "") -> "NoReturn":
    """打印「一句话原因 + 一句该怎么办」并以指定退出码结束。"""
    say(f"[错误] {message}")
    if hint:
        say(f"[怎么办] {hint}")
    raise SystemExit(code)


def console_codepage() -> int:
    """当前控制台的输出代码页（拿不到返回 0）。"""
    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetConsoleOutputCP())
    except (AttributeError, OSError, ValueError):
        return 0


def setup_stdout() -> None:
    """按「控制台代码页」输出，让 cmd / PS5.1 / pwsh7 都不乱码。

    实测：两个 PowerShell 版本都用 `[Console]::OutputEncoding`（来自控制台代码页）
    解码子进程输出；而 Python 往管道里写字时默认用 locale(cp936)。两边不一致就乱码。
    这里让输出编码跟随控制台代码页：65001/无控制台 → UTF-8，其余 → CPxxx。
    """
    for stream in (sys.stdout, sys.stderr):
        # 真正的控制台窗口：不要 reconfigure。Python 3.6+ 对控制台用的是
        # WriteConsoleW（UTF-16），与代码页无关，改成 cp936 反而会乱码。
        try:
            if stream.isatty():
                continue
        except (AttributeError, ValueError):
            pass
        # 被捕获（管道/文件）时一律 UTF-8：DSH、CI、`> out.txt` 都按 UTF-8 读。
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def clean_path(value: str) -> str:
    """去掉调用方误带进来的引号（`cmd /c "x"` 常见）。"""
    text = str(value).strip()
    for quote in ('"', "'"):
        if len(text) >= 2 and text.startswith(quote) and text.endswith(quote):
            text = text[1:-1].strip()
    return text


# ---------------------------------------------------------------- 包定位


def _root_is_valid(root: Path) -> bool:
    return (root / "runtime" / "python" / "python.exe").is_file()


def _root_from_script() -> Path | None:
    """脚本自己往上找：<root>\\scripts 或 <root>\\skill\\paper2zh\\scripts 都能命中。"""
    current = Path(__file__).resolve().parent
    for _ in range(4):
        if _root_is_valid(current):
            return current
        current = current.parent
    return None


def find_root() -> Path:
    """找包根目录；找不到就退出码 2，绝不自己去找别的东西替代。

    顺序：环境变量 → 脚本相对位置 → 配置文件 → 常见路径。
    「脚本相对位置」排在配置前，是因为首次安装时配置文件还不存在。
    """
    tried: list[str] = []

    env_root = os.environ.get("PAPER2ZH_HOME")
    if env_root:
        candidate = Path(env_root).expanduser()
        tried.append(f"环境变量 PAPER2ZH_HOME={candidate}")
        if _root_is_valid(candidate):
            return candidate.resolve()

    relative = _root_from_script()
    if relative is not None:
        return relative.resolve()
    tried.append("脚本所在目录向上 4 层")

    if CFG_FILE.is_file():
        try:
            cfg = json.loads(CFG_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            cfg = {}
        root_value = cfg.get("root") if isinstance(cfg, dict) else None
        if root_value:
            candidate = Path(str(root_value)).expanduser()
            tried.append(f"配置文件 {CFG_FILE} -> {candidate}")
            if _root_is_valid(candidate):
                return candidate.resolve()
    else:
        tried.append(f"配置文件 {CFG_FILE} 不存在")

    for candidate in FALLBACK_ROOTS:
        tried.append(f"常见路径 {candidate}")
        if _root_is_valid(candidate):
            return candidate.resolve()

    fail(
        EXIT_NO_BUNDLE,
        "找不到 PDF 转中文的安装包（Windows 上那套带 runtime 的文件夹）。",
        "请重新双击包里的「1-开始安装.cmd」，它会重新登记安装位置；"
        "如果包已经删了，请重新解压一份。",
    )
    raise AssertionError("unreachable")


def scripts_dir() -> Path:
    """当前脚本所在目录（发行态和开发态都成立）。"""
    return Path(__file__).resolve().parent


def python_exe(root: Path) -> Path:
    exe = root / "runtime" / "python" / "python.exe"
    if not exe.is_file():
        fail(EXIT_NO_BUNDLE, "安装包里的 Python 不见了（可能被杀毒软件删了）。",
             "双击「1-开始安装.cmd」看看具体缺了什么，或者重新解压一份安装包。")
    return exe


def site_packages(root: Path) -> Path:
    return root / "runtime" / "site-packages"


def mineru_home(root: Path) -> Path:
    """MINERU_HOME：MinerU 把配置与权重都放在这里。"""
    return root / "home"


def models_dir(root: Path) -> Path:
    return mineru_home(root) / "models"


def runtime_env(root: Path) -> dict[str, str]:
    """给 MinerU 子进程用的环境变量，全部指向本包内部，不碰系统。"""
    env = dict(os.environ)
    env["MINERU_HOME"] = str(mineru_home(root))
    env.setdefault("MINERU_MODEL_SOURCE", "modelscope")
    env["MINERU_TELEMETRY_DISABLED"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONPATH", None)  # 嵌入式 Python 会忽略它，留着只会误导
    return env


def user_config_path() -> Path:
    return CFG_FILE


def short_stem(stem: str, limit: int = 60) -> str:
    """文件名太长会把 Windows 260 字符路径撑爆，超限就截断 + 8 位哈希。

    实测：121 字符的文件名会让产物目录 + MinerU 的 zip 名字（stem 出现两次）
    超过 260，报错还被换行截断成空字符串，根本没法诊断。
    """
    stem = stem.strip() or "document"
    if len(stem) <= limit:
        return stem
    import hashlib

    digest = hashlib.sha256(stem.encode("utf-8")).hexdigest()[:8]
    keep = max(limit - 9, 8)
    return f"{stem[:keep].rstrip(' .')}-{digest}"


def workdir_for(pdf: Path) -> Path:
    """产物目录：<pdf 所在目录>/翻译结果/<文件名>/，绝不写到用户原目录里。"""
    return pdf.parent / "翻译结果" / short_stem(pdf.stem)


def parse_tempdir(pdf: Path) -> Path:
    """MinerU 的输出先落到短路径临时目录，避免「stem 出现两次」撑爆 260。

    C:\\Users\\…\\Temp\\paper2zh-parse\\<hash8>\\ 约 40 字符，给文件名留足余量。
    """
    import tempfile
    import hashlib

    digest = hashlib.sha256(str(pdf.resolve()).lower().encode("utf-8")).hexdigest()[:8]
    return Path(tempfile.gettempdir()) / "paper2zh-parse" / digest


def write_progress(workdir: Path, stage: str, stage_name: str,
                   current: int, total: int, last_line: str) -> None:
    """给翻译阶段也写 .progress.json，否则 status 会一直停在「分块」。"""
    import json as _json
    from datetime import datetime, timezone

    pct = min(100, int(current * 100 / total)) if total else 0
    payload = {
        "stage": stage,
        "stage_name": stage_name,
        "current": current,
        "total": total,
        "pct": pct,
        "elapsed_sec": 0,
        "eta_sec": None,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_line": last_line,
    }
    try:
        (workdir / ".progress.json").write_text(
            _json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


def ensure_dir(path: Path) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        fail(EXIT_DISK_OR_PERM, f"建不了目录 {path}：{exc.strerror or exc}",
             "检查这个位置是否只读、是否被占用，或者换一个用户有写权限的目录。")
    return path


def free_space_gb(path: Path) -> float:
    import shutil

    try:
        return shutil.disk_usage(path).free / (1024 ** 3)
    except OSError:
        return 0.0


# ---------------------------------------------------------------- 中文路径兼容
#
# 实测（2026-11）：mineru_llama_cpp 用窄字符 API 加载自己的 DLL，
# 安装路径含中文时 standard 档会报
#   No mapping for the Unicode character exists in the target multi-byte code page
# 把 mineru_llama_cpp 复制到纯英文目录、并让它在 sys.path 里排在 site-packages
# 前面，问题消失（ONNX/basic 档不受影响）。
#
# 这里做两件事：把库复制过去、把目录写进 python312._pth。运行中的进程改 _pth 要
# 下次启动才生效，但 MinerU 是子进程启动的，所以在 prepare 里先修再跑即可。

ASCII_LIBS_VERSION_FILE = ".paper2zh-libs-version"


def is_ascii_path(path: Path) -> bool:
    return all(ord(char) < 128 for char in str(path))


def ascii_libs_dir() -> Path:
    """纯英文的推理库目录（放 %LOCALAPPDATA%，与安装位置无关）。"""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "paper2zh" / "libs"


def _patch_pth(python_dir: Path, entry: str) -> bool:
    """把 *entry* 插到 site-packages 之前；返回是否改动。"""
    candidates = sorted(python_dir.glob("python*._pth"))
    if not candidates:
        return False
    pth = candidates[0]
    lines = pth.read_text(encoding="ascii", errors="replace").splitlines()
    if entry in lines:
        return False
    insert_at = len(lines)
    for index, line in enumerate(lines):
        if "site-packages" in line.replace("\\", "/"):
            insert_at = index
            break
    lines.insert(insert_at, entry)
    pth.write_text("\n".join(lines) + "\n", encoding="ascii", newline="\r\n")
    return True


def ensure_ascii_libs(root: Path, *, quiet: bool = True) -> str:
    """安装路径含中文时，把 mineru_llama_cpp 搬到英文目录并登记到 _pth。

    返回一句给人看的说明；路径本来就是英文则返回空字符串。
    """
    import shutil

    if is_ascii_path(root):
        return ""

    source = site_packages(root) / "mineru_llama_cpp"
    if not source.is_dir():
        return ""

    target_root = ascii_libs_dir()
    target = target_root / "mineru_llama_cpp"
    try:
        # 用源目录的修改时间与大小做粗判，避免每次都复制 60 MB
        stamp = f"{source.stat().st_mtime_ns}"
        stamp_file = target_root / ASCII_LIBS_VERSION_FILE
        stale = (
            not target.is_dir()
            or not stamp_file.is_file()
            or stamp_file.read_text(encoding="utf-8").strip() != stamp
        )
        if stale:
            if target.exists():
                shutil.rmtree(target)
            target_root.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target)
            stamp_file.write_text(stamp, encoding="utf-8")
        changed = _patch_pth(python_exe(root).parent, str(target_root))
    except OSError as exc:
        fail(EXIT_DISK_OR_PERM, f"没法把推理库放到英文目录：{exc.strerror or exc}",
             "检查 %LOCALAPPDATA% 是否可写，或直接把整个包挪到纯英文路径。")

    if changed:
        return f"安装路径含中文，推理库已复制到 {target_root} 并登记，standard 档可用"
    return f"安装路径含中文，推理库已在 {target_root}，standard 档可用"


def main(argv: list[str]) -> int:
    """`python paths.py`：打印当前的包定位结果，排障用。"""
    import argparse

    parser = argparse.ArgumentParser(description="包定位工具：打印安装位置与关键路径")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    args = parser.parse_args(argv[1:])

    setup_stdout()
    root = find_root()
    info = {
        "root": str(root),
        "python": str(python_exe(root)),
        "site_packages": str(site_packages(root)),
        "mineru_home": str(mineru_home(root)),
        "models": str(models_dir(root)),
        "config": str(user_config_path()),
        "free_gb": round(free_space_gb(root), 1),
    }
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=1))
    else:
        for key, value in info.items():
            print(f"{key:<14} {value}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))
