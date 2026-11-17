#!/usr/bin/env python3
"""Generate THIRD_PARTY_NOTICES.md from the installed dist-info metadata.

The upstream plan suggested `pip-licenses`, but that would add a build-time
dependency to a bundle we are trying to keep small.  Reading the metadata
directly produces the same table with no extra install.

Usage:
    python build/make_notices.py [--site <runtime/site-packages>] [--out <file>]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HEADER = """# 第三方组件许可（THIRD PARTY NOTICES）

本项目的源代码采用 MIT 许可证（见 `LICENSE`）。

分发包里还包含下列第三方组件。它们各自保留原有许可证；本文件按
`{generated}` 时刻的安装状态生成，供分发与审计使用。

**特别说明：MinerU 不是 MIT 许可证**，它使用 OpenDataLab 的
`LicenseRef-MinerU-Open-Source-License`（Apache-2.0 加附加条款），
详见 <https://github.com/opendatalab/MinerU/blob/master/LICENSE.md>。
本项目的 MIT 许可证只覆盖本项目自己的代码。

| 包名 | 版本 | 许可证 | 项目地址 |
|---|---|---|---|
"""

FOOTER = """
## Python 运行时

嵌入式 CPython `{python}` 采用 PSF 许可证（Python Software Foundation
License Agreement），详见 <https://docs.python.org/3/license.html>。

## 模型权重

模型权重不随轻包分发，由用户执行「1-开始安装.cmd」时从 ModelScope 下载，
其许可证归 OpenDataLab / MinerU 上游所有（同样是 MinerU 开放源码许可证）。
本仓库与分发包中不包含任何模型权重。
"""


def parse_metadata(path: Path) -> dict[str, str]:
    info: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return info
    for line in text.splitlines():
        if not line.strip():
            break
        match = re.match(r"^(Name|Version|License|License-Expression|Summary):\s*(.+)$", line)
        if match:
            info.setdefault(match.group(1), match.group(2).strip())
        link = re.match(r"^Project-URL:\s*([^,]+),\s*(.+)$", line)
        if link and "url" not in info:
            info["url"] = link.group(2).strip()
    if "License" not in info and "License-Expression" in info:
        info["License"] = info["License-Expression"]
    return info


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="生成第三方许可清单")
    parser.add_argument("--site", default=str(root / "runtime" / "site-packages"))
    parser.add_argument("--out", default=str(root / "THIRD_PARTY_NOTICES.md"))
    args = parser.parse_args(argv[1:])

    site = Path(args.site)
    if not site.is_dir():
        print(f"error: no site-packages at {site}", file=sys.stderr)
        return 1

    rows: list[tuple[str, str, str, str]] = []
    for dist in sorted(site.glob("*.dist-info")):
        info = parse_metadata(dist / "METADATA")
        if not info.get("Name"):
            continue
        rows.append(
            (
                info.get("Name", "?"),
                info.get("Version", "?"),
                (info.get("License") or info.get("License-Expression") or "未标注").strip(),
                info.get("url", ""),
            )
        )

    import datetime

    generated = datetime.date.today().isoformat()
    lines = [HEADER.format(generated=generated)]
    for name, version, license_name, url in rows:
        safe_license = license_name.replace("|", "\\|")[:60]
        lines.append(f"| {name} | {version} | {safe_license} | {url} |")
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    lines.append(FOOTER.format(python=python_version))

    out = Path(args.out)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[notices] wrote {out} ({len(rows)} packages)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
