# 让 AI 帮你安装 paper2zh

这份文档是写给 **AI agent** 的：用户可以只丢一个 GitHub 链接，让 AI 完成下载、解压、安装和自检。

> 前提：这个 AI 必须**能执行本机命令**（有终端 / shell 工具），并且运行在 **Windows 10 / 11 x64** 上。
> 纯聊天窗口没有终端权限，装不了——遇到这种 AI，请把本文档的「手动命令」部分转给用户。

---

## 一、直接发给 AI 的话（复制整段）

```text
请帮我在 Windows 上安装 paper2zh（英文论文 PDF 转中文 Markdown 的工具）。

仓库：https://github.com/Hyp3rY/paper2zh
要求：
1. 找到最新 Release 里文件名以 `-win-x64-lite.zip` 结尾的附件（版本号不用管）并下载到临时目录；
2. 解压到 D:\Tools\paper2zh（Windows PowerShell 用 Expand-Archive）；
3. 用包内的 python 在后台启动安装：
   <包根>\runtime\python\python.exe <包根>\skill\paper2zh\scripts\bootstrap.py all
   安装约 5–10 分钟、下载约 2.8 GB，不要阻塞等待；
4. 每 30 秒读一次 <包根>\安装进度.txt 的最后几行，只在阶段变化、出错或完成时告诉我；
5. 装完运行 doctor.py --json，把结论和接下来怎么用（/paper2zh）告诉我；
6. 任何一步失败，把退出码、最后一行输出和我的下一步操作告诉我，不要重试超过两次。
```

`<包根>` 是解压后含 `runtime\python\python.exe` 的那层目录，下面的命令会自动找到它。

---

## 二、AI 应该执行的命令（PowerShell）

### 1. 下载轻包

```powershell
$repo = "Hyp3rY/paper2zh"
$release = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest"
$asset = $release.assets |
    Where-Object { $_.name -like "*-win-x64-lite.zip" } |
    Select-Object -First 1
if (-not $asset) { throw "最新 Release 里没有 lite 附件，请改用手动下载" }

$zip = Join-Path $env:TEMP "paper2zh-lite.zip"
Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $zip
```

> 这里**不写死版本号和下载地址**：每次都从「最新 Release」里挑文件名匹配的附件。
> 如果 GitHub API 被限流或返回 404，就让用户打开
> <https://github.com/Hyp3rY/paper2zh/releases/latest> 手动下载，或改用完整离线包。

### 2. 解压

```powershell
New-Item -ItemType Directory -Force -Path "D:\Tools" | Out-Null
Expand-Archive -Path $zip -DestinationPath "D:\Tools" -Force

# 不写死解压后的目录名（zip 里通常带一层版本号）
$root = Get-ChildItem "D:\Tools" -Directory |
    Where-Object { Test-Path (Join-Path $_.FullName "runtime\python\python.exe") } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $root) { throw "解压后找不到 runtime\python\python.exe" }
$root
```

### 3. 后台启动安装（不要等它跑完）

```powershell
$python = Join-Path $root "runtime\python\python.exe"
$script = Join-Path $root "skill\paper2zh\scripts\bootstrap.py"
Start-Process -FilePath $python -ArgumentList $script, "all" `
  -WorkingDirectory $root -WindowStyle Hidden
```

### 4. 轮询进度（每 30 秒一次）

```powershell
Get-Content (Join-Path $root "安装进度.txt") -Tail 5
Test-Path (Join-Path $root "home\.warmed")   # True 表示装完
```

**只在以下时刻向用户汇报**：

- 阶段变化（`[1/4]` → `[2/4]` → `[3/4]` → `[4/4]`）；
- 报错 / 退出；
- 完成。

中间的「下载进度 40%」不需要每 10 秒念一遍。

### 5. 装进 AI 工具

```powershell
& $python (Join-Path $root "skill\paper2zh\scripts\install_skill.py")
```

### 6. 自检并汇报

```powershell
& $python (Join-Path $root "skill\paper2zh\scripts\doctor.py") --json
```

把 `ok`、失败的检查项和「该怎么办」转述给用户，不要贴原始 JSON。

---

## 三、安装过程中会发生什么

| 阶段 | 做什么 | 耗时 | 进度写到哪 |
|---|---|---|---|
| `[1/4]` | 检查磁盘、测速 pip 镜像、探测 ModelScope | 10–30 秒 | 控制台 + `安装进度.txt` |
| `[2/4]` | 下载依赖（约 0.8 GB） | 2–5 分钟 | 同上，含「下载进度 x%」 |
| `[3/4]` | 下载模型（约 2 GB，ModelScope） | 3–8 分钟 | 同上 |
| `[4/4]` | 预热模型（跑一页样例） | 1–3 分钟 | 同上 |

- 镜像会**先实测吞吐量再选最快的**；安装失败会自动换下一个源重试；
- 三个断点标记：`runtime\.deps-ok`、`home\models\.models-ok`、`home\.warmed`。中断后重跑会跳过已完成步骤；
- 全程不需要管理员权限。

---

## 四、常见卡点与处理

| 现象 | 原因 | 让 AI 做什么 |
|---|---|---|
| 下载 404 | Release 没有 lite 附件 | 打开 Releases 页面确认文件名，或改用完整包 |
| 解压后找不到 `runtime\python` | zip 里多套了一层同名目录 | 用 `Get-ChildItem` 找到真正含 `runtime` 的那层 |
| 30 秒没有任何新行 | 网络慢 / 杀软扫描 / 模型加载 | 继续等；超过 3 分钟看 `安装进度.txt` 最后一行，必要时重跑（会断点续传） |
| 退出码 9 | 网络不可达 | 检查代理/DNS，重跑 |
| 退出码 10 | 依赖装不上 | 换网络或用完整离线包 |
| 退出码 11 | 模型下载失败 | 重跑（断点续传）；仍失败用完整包 |
| 退出码 12 | 磁盘不足 | 清理到 ≥ 6 GB |
| 双击 `.cmd` 被拦 | 安全策略/EDR | 不要用 `.cmd`，直接按上面的 `python.exe ...` 调用 |

**测速单独看**（不安装，只测试镜像速度）：

```powershell
& $python (Join-Path $root "skill\paper2zh\scripts\bootstrap.py") probe
```

---

## 五、装完之后

让用户在 AI 对话框里输入：

```text
/paper2zh 把 D:\论文\xxx.pdf 翻成中文
```

产物在 PDF 旁边的 `翻译结果\<文件名>\`：`中文版.md`、`中英对照.md`、`images\`、`qa.json`、`先读我.txt`。

---

## 六、升级旧版本

paper2zh **没有自动升级**。升级时不要覆盖旧目录，建议按下面做：

1. 按第一、二节下载并解压**最新** lite 包到一个新目录，例如 `D:\Tools\paper2zh-<新版本>`；
2. 在新目录里再跑一次安装：

   ```powershell
   $python = Join-Path $root "runtime\python\python.exe"
   $script = Join-Path $root "skill\paper2zh\scripts\bootstrap.py"
   & $python $script all
   ```

   它会重新登记 skill，覆盖旧的 skill 文件；`%USERPROFILE%\.paper2zh` 里的配置和已有的「翻译结果」不会动；
3. 运行 `doctor.py --json` 自检；
4. 确认新版本可用后，再删除旧目录。

如果确实要换到原目录：先关闭旧目录里正在运行的程序，把新版解压覆盖进去，再执行一次 `bootstrap.py all`。

> 旧版本留下的 `中文版.md` / `中英对照.md` 不会被自动改名；新版本 `finish` 后产出 `中文版.md` / `中英对照.md`。
> 升级只换程序文件，不动用户已经翻好的内容。

## 七、给 AI 的汇报模板

```text
安装完成（耗时 X 分 Y 秒）：
- 依赖：完成
- 模型：完成（standard 档）
- 预热：完成
- 自检：9 项全部通过 / 有 N 项需要注意：…

接下来在对话框输入：
/paper2zh 把 <PDF 路径> 翻成中文
```
