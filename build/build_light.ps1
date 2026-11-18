# 构建轻包（Windows）：runtime + skill + 启动器，约 50 MB。
#
#   pwsh -File build\build_light.ps1 [-Version 1.2.0] [-OutDir build\out] [-SkipZip]
#
# 轻包里只有两样「大」东西：嵌入式 Python 与源码。模型和 site-packages 里的
# 依赖不进来（site-packages 反而要进来——它才是依赖本体）；模型由
# 「1-开始安装.cmd」在用户机器上下载。

param(
    [string]$Version = "1.2.0",
    [string]$OutDir = "",
    [switch]$SkipZip
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $OutDir) { $OutDir = Join-Path $Root "build\out" }
$Name = "paper2zh-v$Version-win-x64-lite"
$Stage = Join-Path $OutDir $Name

function Assert-File([string]$Path, [string]$Hint) {
    if (-not (Test-Path $Path)) { throw "缺少 $Path —— $Hint" }
}

Write-Host "[build] 源目录：$Root"

# ---------------------------------------------------------------- 校验
Assert-File (Join-Path $Root "runtime\python\python.exe") "先跑一次 1-开始安装.cmd，或把 runtime 放回来"
Assert-File (Join-Path $Root "runtime\site-packages\mineru") "依赖没装（runtime\.deps-ok 存在吗？）"
Assert-File (Join-Path $Root "requirements.lock") "缺少依赖锁文件"
Assert-File (Join-Path $Root "SKILL.md") "缺少 SKILL.md"
Assert-File (Join-Path $Root "THIRD_PARTY_NOTICES.md") "先跑 build\make_notices.py"

# pip wheel：给「没有 pip 的嵌入式 Python」用的 bootstrap 组件
$PipWheel = Get-ChildItem (Join-Path $Root ".cache") -Filter "pip-*.whl" -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | Select-Object -First 1
Assert-File ($PipWheel.FullName) "先在 .cache 里放一个 pip wheel（bootstrap 要用）"

# ---------------------------------------------------------------- 清理
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

function Copy-Tree([string]$Source, [string]$Destination) {
    $parent = Split-Path -Parent $Destination
    if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
    Copy-Item $Source $Destination -Recurse -Force
}

Write-Host "[build] 复制运行时（只要嵌入式 Python，依赖由引导安装下载）…"
$RuntimeDir = Join-Path $Stage "runtime"
New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
Copy-Tree (Join-Path $Root "runtime\python") (Join-Path $RuntimeDir "python")
# 依赖与完成标记不进轻包：site-packages 由「1-开始安装.cmd」现场装
New-Item -ItemType Directory -Force -Path (Join-Path $RuntimeDir "site-packages") | Out-Null
Get-ChildItem (Join-Path $RuntimeDir "python") -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "[build] 复制 skill 源码…"
$SkillDir = Join-Path $Stage "skill\paper2zh"
New-Item -ItemType Directory -Force -Path (Join-Path $SkillDir "scripts") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $SkillDir "references") | Out-Null
Copy-Item (Join-Path $Root "SKILL.md") (Join-Path $SkillDir "SKILL.md") -Force
Copy-Item (Join-Path $Root "scripts\*.py") (Join-Path $SkillDir "scripts") -Force
Copy-Item (Join-Path $Root "references\*") (Join-Path $SkillDir "references") -Force
Get-ChildItem (Join-Path $SkillDir "scripts") -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force

Write-Host "[build] 复制启动器与文档…"
foreach ($file in @("1-开始安装.cmd", "paper2zh.cmd",
                    "AI-INSTALL.md", "requirements.lock", "LICENSE", "THIRD_PARTY_NOTICES.md", "MANIFEST.json")) {
    Copy-Item (Join-Path $Root $file) (Join-Path $Stage $file) -Force
}
Copy-Item (Join-Path $Root "README-user.md") (Join-Path $Stage "先读我.txt") -Force

Write-Host "[build] 打包 pip 引导组件与样例…"
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "bootstrap") | Out-Null
Copy-Item $PipWheel.FullName (Join-Path $Stage "bootstrap\pip.whl") -Force
New-Item -ItemType Directory -Force -Path (Join-Path $Stage "samples") | Out-Null
Copy-Item (Join-Path $Root "samples\one-page.pdf") (Join-Path $Stage "samples\one-page.pdf") -Force

# ---------------------------------------------------------------- 元数据
$sha = (Get-FileHash (Join-Path $Root "requirements.lock") -Algorithm SHA256).Hash.ToLower()
$manifest = [ordered]@{
    bundle = "paper2zh"
    bundle_version = $Version
    platform = "win-x64"
    python = "3.12.10"
    mineru = "4.2.2"
    requirements_sha256 = $sha
    models = [ordered]@{ tier = "standard"; source = "modelscope"; approx_size_gb = 2.0 }
    built_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    builder = "build/build_light.ps1"
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $Stage "MANIFEST.json") -Encoding utf8
@{ version = $Version; built_at = $manifest.built_at } |
    ConvertTo-Json | Set-Content (Join-Path $Stage "VERSION.json") -Encoding utf8

# ---------------------------------------------------------------- 体积
$bytes = (Get-ChildItem $Stage -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ("[build] 目录体积：{0:N1} MB（{1:N0} 个文件）" -f ($bytes / 1MB), (Get-ChildItem $Stage -Recurse -File).Count)
if ($bytes -gt 100MB) { throw "轻包超过 100 MB，检查是不是把模型或缓存打进去了" }

if (-not $SkipZip) {
    $zip = Join-Path $OutDir "$Name.zip"
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Compress-Archive -Path $Stage -DestinationPath $zip -CompressionLevel Optimal
    $zipMb = (Get-Item $zip).Length / 1MB
    Write-Host ("[build] 完成：{0}（{1:N1} MB）" -f $zip, $zipMb)
}

Write-Host "[build] 目录：$Stage"

