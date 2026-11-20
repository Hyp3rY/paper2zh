# 构建完整离线包（Windows）：轻包 + 依赖 site-packages + 模型。
#
#   pwsh -File build\build_full.ps1 [-Version 1.2.2] [-OutDir build\out] [-SkipZip]
#
# 完整包给完全断网的内网用：解压 → 双击 1-开始安装.cmd（会跳过已就绪的步骤）
# → 双击 1-开始安装.cmd（会自动装进 AI 工具并自检）。约 3 GB。
#
# 不打包本机状态：home\config.yaml（ONNX 设备校准）、home\warmup、home\.warmed、
# home\tier.txt 都是「这台机器」的，换机必须重新生成。

param(
    [string]$Version = "1.2.2",
    [string]$OutDir = "",
    [switch]$SkipZip
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
if (-not $OutDir) { $OutDir = Join-Path $Root "build\out" }
$LiteName = "paper2zh-v$Version-win-x64-lite"
$FullName = "paper2zh-v$Version-win-x64-full"
$LiteStage = Join-Path $OutDir $LiteName
$Stage = Join-Path $OutDir $FullName
$Zip = Join-Path $OutDir "$FullName.zip"

function Write-Step([string]$Text) { Write-Host "[full] $Text" }

function Invoke-Robocopy([string]$Source, [string]$Destination, [string[]]$Extra = @()) {
    $arguments = @($Source, $Destination, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/R:1", "/W:1") + $Extra
    & robocopy @arguments | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "robocopy 失败（exit $LASTEXITCODE）：$Source -> $Destination"
    }
}

Write-Step "1/5 构建轻包作为底子…"
& pwsh -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "build\build_light.ps1") `
    -Version $Version -OutDir $OutDir
if ($LASTEXITCODE -ne 0) { throw "轻包构建失败" }

Write-Step "2/5 复制依赖（site-packages）…"
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
Copy-Item $LiteStage $Stage -Recurse -Force
Invoke-Robocopy (Join-Path $Root "runtime\site-packages") (Join-Path $Stage "runtime\site-packages") `
    @("/XD", "__pycache__", "/XF", "*.pyc")

Write-Step "3/5 复制模型（约 2 GB）…"
Invoke-Robocopy (Join-Path $Root "home\models") (Join-Path $Stage "home\models") `
    @("/XD", ".locks")

Write-Step "4/5 写完成标记、清掉本机状态…"
Copy-Item (Join-Path $Root "runtime\.deps-ok") (Join-Path $Stage "runtime\.deps-ok") -Force
$python = Join-Path $Root "runtime\python\python.exe"
$env:MINERU_HOME = Join-Path $Root "home"
$verify = & $python -m mineru.kit.main models verify --tier standard 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) { throw "模型校验失败，不能打包：$verify" }
Set-Content -Path (Join-Path $Stage "home\models\.models-ok") -Value $verify -Encoding utf8
foreach ($relative in @("home\config.yaml", "home\tier.txt", "home\.warmed")) {
    Remove-Item (Join-Path $Stage $relative) -Force -ErrorAction SilentlyContinue
}
Remove-Item (Join-Path $Stage "home\warmup") -Recurse -Force -ErrorAction SilentlyContinue

# 绝对不能把构建机的绝对路径带出去
$leak = Get-ChildItem $Stage -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in ".pth", ".py", ".json", ".txt", ".cmd", ".yaml", ".csv" } |
    Select-String -SimpleMatch $Root -List |
    Select-Object -First 3
if ($leak) {
    throw "包里残留了构建机绝对路径：$($leak.Path -join ', ')"
}

if (-not $SkipZip) {
    Write-Step "5/5 压缩（约 3 GB，需要几分钟）…"
    if (Test-Path $Zip) { Remove-Item $Zip -Force }
    & tar -a -c -f $Zip -C $OutDir $FullName
    if ($LASTEXITCODE -ne 0) { throw "压缩失败（tar exit $LASTEXITCODE）" }
} else {
    Write-Step "已跳过压缩（-SkipZip）"
}

function Get-DirSize([string]$Path) {
    if (-not (Test-Path $Path)) { return 0 }
    return (Get-ChildItem $Path -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object Length -Sum).Sum
}

$stageSize = (Get-DirSize $Stage) / 1GB
if (-not $SkipZip) {
    $zipSize = (Get-Item $Zip).Length / 1GB
    Write-Host ("[full] 完成：{0}（{1:N2} GB，解压后 {2:N2} GB）" -f $Zip, $zipSize, $stageSize)
} else {
    Write-Host ("[full] 完成：{0}（{1:N2} GB，未压缩）" -f $Stage, $stageSize)
}
