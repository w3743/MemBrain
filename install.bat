@echo off
chcp 65001 >nul
title CSM Agent 安装程序
echo.
echo ╔══════════════════════════════════════╗
echo ║   CSM Agent 一键安装程序            ║
echo ╚══════════════════════════════════════╝
echo.

:: 检查 Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)
python --version

:: 安装 CSM
echo.
echo [1/2] 安装 csm-agent ...
pip install git+https://github.com/yourname/csm-agent.git
if %errorlevel% neq 0 (
    echo [警告] 远程安装失败，尝试从本地安装...
    cd /d "%~dp0"
    pip install .
)

:: 安装嵌入模型（可选）
echo.
echo [2/2] 安装语义向量模型（BGE-large-zh, 约 1.3GB）？
echo    Y = 完整安装（推荐，支持语义搜索）
echo    N = 基础安装（仅关键词搜索）
choice /c YN /n /m "选择 [Y/N]: "
if %errorlevel% equ 2 goto :skip_embed

set HF_ENDPOINT=https://hf-mirror.com
echo 使用 HuggingFace 镜像下载模型...
pip install sentence-transformers
if %errorlevel% neq 0 (
    echo [警告] 模型安装失败，基础功能仍可用
)

:skip_embed
:: 配置 DeepSeek
echo.
set /p API_KEY="DeepSeek API Key（留空跳过）: "
if not "%API_KEY%"=="" (
    echo {"api_key": "%API_KEY%", "model": "deepseek-v4-flash"} > "%USERPROFILE%\.csm_llm_config.json"
    echo API Key 已保存
)

echo.
echo ══════════════════════════════════════
echo 安装完成！
echo.
echo 启动管理控制台: csm-agent serve
echo 浏览器打开: http://127.0.0.1:8765
echo.
echo 日常使用只需启动一次，后台长期运行。
echo ══════════════════════════════════════
pause
