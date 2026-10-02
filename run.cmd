@echo off
rem ===========================================================================
rem  douban-reader 统一入口
rem  用法：
rem      run.cmd                      使用 config.toml 的默认范围抓取
rem      run.cmd --rebuild-only       只用已缓存的 pages/ 重新生成章节（不联网）
rem      run.cmd --dry-run            只打印生效配置与抓取计划
rem      run.cmd --start 1 --end 20 --headless
rem ===========================================================================
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
    echo [warn] 未找到 .venv，回退到系统 python
)

"%PY%" -m douban_reader %*
exit /b %ERRORLEVEL%
