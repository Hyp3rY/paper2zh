#!/usr/bin/env python3
"""引导安装：预检 → 装依赖 → 下模型 → 预热。

用户只会双击「1-开始安装.cmd」，所以这里必须做到：
  - 全程中文、说人话，任何失败都给「下一步怎么办」，不许甩 traceback；
  - 每 20 秒至少一行输出，同时写根目录「安装进度.txt」；
  - 断点续跑：runtime\\.deps-ok、home\\models\\.models-ok、home\\.warmed 三个标记；
  - 不写注册表、不改系统 PATH、不装服务、不请求管理员权限。

用法：
    python bootstrap.py [all|deps|models|verify|probe] [--mirror cn|global]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# 嵌入式 Python 的 ._pth 会关掉「脚本目录自动进 sys.path」，这里显式登记一次。
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import paths  # noqa: E402  本地模块

PIP_MIRRORS = {
    "cn": [
        ("清华", "https://pypi.tuna.tsinghua.edu.cn/simple"),
        ("阿里", "https://mirrors.aliyun.com/pypi/simple"),
        ("中科大", "https://pypi.mirrors.ustc.edu.cn/simple"),
    ],
    "global": [("PyPI 官方", "https://pypi.org/simple")],
}
MODEL_SOURCE = "modelscope"

MIN_FREE_GB = 6.0
MARK_DEPS = "runtime/.deps-ok"
MARK_MODELS = "home/models/.models-ok"
MARK_WARM = "home/.warmed"


# ---------------------------------------------------------------- 输出


class Console:
    """心跳式输出：控制台 + 安装进度.txt 双写。"""

    def __init__(self, root: Path):
        self.root = root
        self.log = root / "安装进度.txt"
        self.last = 0.0
        self.start = time.time()

    def _append(self, line: str) -> None:
        try:
            with self.log.open("a", encoding="utf-8") as handle:
                handle.write(f"[{time.strftime('%H:%M:%S')}] {line}\n")
        except OSError:
            pass

    def say(self, line: str, *, force: bool = True) -> None:
        print(line, flush=True)
        if force:
            self._append(line)
        self.last = time.time()

    def beat(self, message: str) -> None:
        """长时间没输出时，定期说一句废话，避免用户以为卡死。"""
        if time.time() - self.last >= 20:
            elapsed = int(time.time() - self.start)
            print(f"    {message}（已等待 {elapsed // 60} 分 {elapsed % 60} 秒）", flush=True)
            self._append(message)
            self.last = time.time()


def banner() -> None:
    print("=" * 44)
    print("  PDF 转中文 - 安装程序")
    print("  第一次使用请耐心等待，大约 5 分钟")
    print("=" * 44)
    print()


# ---------------------------------------------------------------- 预检


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_network(url: str, timeout: float = 6.0) -> tuple[bool, float]:
    started = time.time()
    try:
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=timeout):
            return True, time.time() - started
    except (urllib.error.URLError, TimeoutError, OSError):
        return False, time.time() - started


def probe_throughput(url: str, timeout: float = 20.0) -> tuple[int, float]:
    """真下一段索引页，返回 (字节数, 秒)。HEAD 的延迟不能代表吞吐量。"""
    started = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            total = 0
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total >= 2 * 1024 * 1024:  # 2 MB 封顶，够算了
                    break
            return total, time.time() - started
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, time.time() - started


def rank_pip_mirrors(mirror: str, console: Console) -> list[tuple[str, str, float]]:
    """实测各镜像吞吐量并排序；HEAD 快不代表下载快（阿里会限速）。

    返回 [(名称, 地址, KB/s), ...]，最快的在前，其余留作装依赖失败时的备选。
    """
    candidates = PIP_MIRRORS["cn" if mirror == "cn" else "global"]
    scored: list[tuple[str, str, float]] = []
    for name, url in candidates:
        if not probe_network(url)[0]:
            console.say(f"    测速 {name}… 不通", force=False)
            continue
        size, elapsed = probe_throughput(f"{url.rstrip('/')}/pip/")
        if size == 0:
            console.say(f"    测速 {name}… 无法测速", force=False)
            continue
        rate = size / max(elapsed, 0.05) / 1024
        console.say(f"    测速 {name}… {rate:,.0f} KB/s", force=False)
        scored.append((name, url, rate))
    if not scored:
        paths.fail(paths.EXIT_NETWORK, "连不上任何软件源。",
                   "检查网络（是否需要代理），然后重新双击「1-开始安装.cmd」。")
    scored.sort(key=lambda item: item[2], reverse=True)
    best = scored[0]
    if len(scored) > 1:
        console.say(f"    选 {best[0]}（{best[2]:,.0f} KB/s），另 {len(scored) - 1} 个源备用",
                    force=False)
    else:
        console.say(f"    选 {best[0]}（{best[2]:,.0f} KB/s）", force=False)
    return scored


def precheck(root: Path, console: Console, mirror: str) -> list[tuple[str, str, float]]:
    console.say("[1/4] 检查磁盘与网络…")
    free = paths.free_space_gb(root)
    if free < MIN_FREE_GB:
        paths.fail(paths.EXIT_DISK_TOO_SMALL, f"磁盘只剩 {free:.1f} GB，安装需要 6 GB。",
                   "清理磁盘后再双击「1-开始安装.cmd」。")
    console.say(f"    磁盘可用 {free:.0f} GB，通过")

    mirrors = rank_pip_mirrors(mirror, console)
    if mirror == "cn":
        ok, elapsed = probe_network("https://www.modelscope.cn/")
        if not ok:
            paths.fail(paths.EXIT_NETWORK, "连不上模型站（ModelScope）。",
                       "换个网络或稍后再试；实在不行找同事要「完整离线包」。")
        console.say(f"    ModelScope 可用（{elapsed:.1f} 秒）")
    return mirrors


# ---------------------------------------------------------------- 依赖


def deps_ready(root: Path, lock: Path) -> bool:
    marker = root / MARK_DEPS
    if not marker.is_file() or not lock.is_file():
        return False
    try:
        return marker.read_text(encoding="utf-8").strip() == sha256_file(lock)
    except OSError:
        return False


def bootstrap_pip(root: Path, console: Console) -> None:
    """嵌入式 Python 没有 pip：用随包附带的 wheel 解压安装，不联网、不生成 shim。"""
    python = paths.python_exe(root)
    probe = subprocess.run([str(python), "-m", "pip", "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if probe.returncode == 0:
        return
    wheel = root / "bootstrap" / "pip.whl"
    if not wheel.is_file():
        candidates = sorted((root / "bootstrap").glob("pip-*.whl")) if (root / "bootstrap").is_dir() else []
        wheel = candidates[0] if candidates else wheel
    if not wheel.is_file():
        paths.fail(paths.EXIT_DEPS_FAILED, "安装包里的 pip 组件缺失，装不了依赖。",
                   "重新解压一份安装包；仍失败请找维护者要完整包。")
    console.say(f"    初始化包管理器（{wheel.name}）…", force=False)
    target = paths.site_packages(root)
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(wheel, root / "bootstrap" / "pip.zip")
    shutil.unpack_archive(str(root / "bootstrap" / "pip.zip"), str(target), "zip")


def install_deps(root: Path, lock: Path, mirrors: list[tuple[str, str, float]],
                 console: Console) -> None:
    console.say("[2/4] 安装依赖（首次约 1 GB，请耐心等）…")
    bootstrap_pip(root, console)
    python = paths.python_exe(root)
    target = paths.site_packages(root)

    def run_pip(index_url: str, extra: list[str], stream: bool) -> tuple[int, list[str]]:
        command = [str(python), "-m", "pip", "install", "--target", str(target),
                   "--no-warn-script-location", "--no-build-isolation",
                   "--disable-pip-version-check", "-i", index_url,
                   "--trusted-host", re.sub(r"^https?://([^/]+).*$", r"\1", index_url)]
        if not stream:
            done = subprocess.run(command + extra, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
            return done.returncode, (done.stdout or "").splitlines()
        process = subprocess.Popen(command + extra, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True,
                                   encoding="utf-8", errors="replace")
        assert process.stdout is not None
        tail: list[str] = []
        last_percent = -10
        for line in process.stdout:
            clean = line.strip()
            if not clean:
                continue
            tail.append(clean)
            tail = tail[-8:]
            match = re.search(r"(\d{1,3})%", clean)
            if match and int(match.group(1)) >= last_percent + 10:
                last_percent = int(match.group(1))
                console.say(f"    下载进度 {last_percent}%", force=False)
            console.beat("仍在安装依赖，网络较慢，请耐心等待")
        return process.wait(), tail

    detail = "未知错误"
    tried: list[str] = []
    for attempt, (name, url, rate) in enumerate(mirrors, start=1):
        if attempt > 1:
            console.say(f"    换用{name}源重试（{rate:,.0f} KB/s）…")
        tried.append(name)
        # 先补 setuptools/wheel：--no-build-isolation 要用它们构建 jieba
        run_pip(url, ["setuptools", "wheel"], stream=False)
        code, tail = run_pip(url, ["-r", str(lock)], stream=True)
        if code == 0:
            (root / MARK_DEPS).write_text(sha256_file(lock), encoding="utf-8")
            console.say("    依赖安装完成")
            return
        detail = tail[-1] if tail else detail
        console.say(f"    {name}源失败：{detail[:120]}")

    paths.fail(paths.EXIT_DEPS_FAILED, f"依赖装不上：{detail[:200]}",
               f"已试过 {'、'.join(tried)}。检查网络后重新双击「1-开始安装.cmd」；"
               "已下载的部分不会丢。")


def models_ready(root: Path) -> bool:
    return (root / MARK_MODELS).is_file()


def download_models(root: Path, console: Console) -> None:
    console.say("[3/4] 下载模型（约 2 GB，走 ModelScope）…")
    python = paths.python_exe(root)
    env = paths.runtime_env(root)
    command = [str(python), "-m", "mineru.kit.main", "models", "download",
               "--tier", "standard", "--source", MODEL_SOURCE]
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True,
                               encoding="utf-8", errors="replace")
    assert process.stdout is not None
    last_percent = -10
    for line in process.stdout:
        clean = line.strip()
        match = re.search(r"(\d{1,3})%", clean)
        if match and int(match.group(1)) >= last_percent + 5:
            last_percent = int(match.group(1))
            console.say(f"    下载进度 {last_percent}%", force=False)
        console.beat("仍在下载模型，网络较慢，请耐心等待")
    code = process.wait()
    if code != 0:
        paths.fail(paths.EXIT_MODEL_DOWNLOAD, "模型下载失败（连接 ModelScope 超时或被中断）。",
                   "已经下载的部分不会丢，检查网络后重新双击「1-开始安装.cmd」。")

    console.say("    校验模型…")
    verify = subprocess.run(
        [str(python), "-m", "mineru.kit.main", "models", "verify", "--tier", "standard"],
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900,
    )
    if verify.returncode != 0:
        paths.fail(paths.EXIT_MODEL_MISSING, "模型下载完了但校验不过。",
                   "重新双击「1-开始安装.cmd」补齐缺的文件。")
    (root / MARK_MODELS).write_text(verify.stdout[-2000:], encoding="utf-8")
    console.say("    模型就绪")


# ---------------------------------------------------------------- 预热


def warmup(root: Path, console: Console) -> None:
    console.say("[4/4] 预热模型（首次加载要几分钟，属于正常现象）…")
    sample = root / "samples" / "one-page.pdf"
    if not sample.is_file():
        console.say("    包内没有预热样例，跳过（不影响使用）")
        return
    python = paths.python_exe(root)
    out = root / "home" / "warmup"
    process = subprocess.Popen(
        [str(python), "-m", "mineru.kit.main", "parse", str(sample),
         "-o", str(out), "-f", "markdown", "--pages", "1", "--tier", "basic"],
        env=paths.runtime_env(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    assert process.stdout is not None
    for _ in process.stdout:
        console.beat("仍在预热模型，第一次会很慢，请耐心等待")
    code = process.wait()
    if code != 0:
        paths.fail(paths.EXIT_MODEL_MISSING, "预热失败，模型可能不完整。",
                   "双击「1-开始安装.cmd」看具体哪里缺；或重跑安装。")
    (root / MARK_WARM).write_text("ok", encoding="utf-8")
    console.say("    预热完成")


# ---------------------------------------------------------------- 入口


def print_status(root: Path) -> None:
    def mark(path: str) -> str:
        return "已完成" if (root / path).exists() else "未完成"

    print()
    print("当前安装状态：")
    print(f"  依赖：{mark(MARK_DEPS)}   模型：{mark(MARK_MODELS)}   预热：{mark(MARK_WARM)}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="PDF 转中文 安装程序（双击「1-开始安装.cmd」即可）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("mode", nargs="?", default="all",
                        choices=["all", "deps", "models", "verify", "probe"],
                        help="all=全部；deps=只装依赖；models=只下模型；verify=只检查；probe=只测速")
    parser.add_argument("--mirror", default="cn", choices=["cn", "global"],
                        help="cn=国内镜像（默认）；global=海外用户走官方源")
    args = parser.parse_args(argv[1:])

    paths.setup_stdout()
    root = paths.find_root()
    paths.runtime_env(root)  # 初始化环境变量（顺带校验路径）

    banner()
    console = Console(root)
    console.say(f"安装位置：{root}", force=False)
    print()

    lock = root / "requirements.lock"
    if not lock.is_file():
        paths.fail(paths.EXIT_DEPS_FAILED, "安装包里缺少 requirements.lock。",
                   "重新解压一份安装包。")

    if args.mode in ("all", "deps", "probe"):
        mirrors = precheck(root, console, args.mirror)
        if args.mode == "probe":
            console.say("\n测速完成；安装时会用最快的那个源，失败会自动换下一个。")
            return paths.EXIT_OK
        if args.mode == "all":
            console.say("")
        if deps_ready(root, lock):
            console.say("[2/4] 依赖已经装好，跳过")
        else:
            install_deps(root, lock, mirrors, console)
        if args.mode == "deps":
            print_status(root)
            console.say("\n依赖已就绪。下一步：双击「1-开始安装.cmd」完成注册。")
            return paths.EXIT_OK

    if args.mode in ("all", "models"):
        if models_ready(root):
            console.say("[3/4] 模型已经下好，跳过")
        else:
            download_models(root, console)
        if not (root / MARK_WARM).is_file():
            warmup(root, console)
        if args.mode == "models":
            print_status(root)
            console.say("\n模型已就绪。下一步：双击「1-开始安装.cmd」完成注册。")
            return paths.EXIT_OK

    if args.mode == "verify":
        import doctor
        return doctor.main(["doctor"])

    if not (root / MARK_WARM).is_file():
        warmup(root, console)

    print_status(root)
    print()
    console.say("环境全部就绪。")
    return paths.EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))

