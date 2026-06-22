# CSM Memory for pi Agent — 一键安装脚本
# 用法：以管理员身份运行此脚本，或右键 "使用 PowerShell 运行"

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host @"
╔══════════════════════════════════════════════════════╗
║     CSM Memory ↔ pi Agent 集成安装脚本              ║
╚══════════════════════════════════════════════════════╝
"@ -ForegroundColor Cyan

# ── 1. 检查前置条件 ──────────────────────────────────────
Write-Host "`n[1/4] 检查前置条件..." -ForegroundColor Yellow

try {
    python --version 2>&1 | Out-Null
    Write-Host "  ✓ Python 可用" -ForegroundColor Green
} catch {
    Write-Host "  ✗ Python 未找到，请先安装 Python 3.10+" -ForegroundColor Red
    exit 1
}

# 检查 CSM 项目
$csmDir = (Get-Item $scriptDir).Parent.FullName
if (-not (Test-Path "$csmDir\src\csm_agent\server.py")) {
    Write-Host "  ✗ 未找到 CSM 项目，请确认脚本位于 pi-extension/ 目录中" -ForegroundColor Red
    exit 1
}
Write-Host "  ✓ CSM 项目: $csmDir" -ForegroundColor Green

# ── 2. 设置环境变量 ──────────────────────────────────────
Write-Host "`n[2/4] 设置环境变量..." -ForegroundColor Yellow

[Environment]::SetEnvironmentVariable("CSM_PROJECT_DIR", $csmDir, "User")
Write-Host "  ✓ CSM_PROJECT_DIR = $csmDir" -ForegroundColor Green

# ── 3. 创建符号链接 ──────────────────────────────────────
Write-Host "`n[3/4] 创建扩展符号链接..." -ForegroundColor Yellow

$extDir = "$env:USERPROFILE\.pi\agent\extensions"
if (-not (Test-Path $extDir)) {
    New-Item -ItemType Directory -Path $extDir -Force | Out-Null
}

$linkPath = "$extDir\csm-memory.ts"
$targetPath = "$scriptDir\csm-memory.ts"

if (Test-Path $linkPath) {
    Write-Host "  ! 扩展链接已存在，跳过" -ForegroundColor Yellow
} else {
    try {
        New-Item -ItemType SymbolicLink -Path $linkPath -Target $targetPath -Force | Out-Null
        Write-Host "  ✓ 符号链接已创建: $linkPath" -ForegroundColor Green
    } catch {
        # 符号链接失败时回退到复制
        Copy-Item $targetPath $linkPath -Force
        Write-Host "  ✓ 已复制扩展文件到: $linkPath" -ForegroundColor Green
    }
}

# ── 4. 验证配置 ──────────────────────────────────────────
Write-Host "`n[4/4] 验证配置..." -ForegroundColor Yellow

Write-Host "  ! 请在新终端中运行 pi，扩展将自动加载" -ForegroundColor Yellow
Write-Host "  ! 在 pi 中输入 /csm-health 验证集成状态" -ForegroundColor Yellow

Write-Host @"

安装完成！ ✅

启动 pi:
  pi

安装后请重启终端以使环境变量生效。
在 pi 中可使用以下命令：
  /remember <内容>    手动存入记忆
  /csm-health         查看记忆健康状态
  /csm-search <查询>   搜索记忆库

要启用自动记忆提取，请配置 DeepSeek API Key：
  [Environment]::SetEnvironmentVariable("CSM_DEEPSEEK_API_KEY", "sk-your-key", "User")

"@ -ForegroundColor Cyan
