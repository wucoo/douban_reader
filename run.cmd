@echo off
rem ===========================================================================
rem  douban-reader 统一入口
rem  注意：本文件必须保持 CRLF 换行 + UTF-8 无 BOM（批处理对换行敏感）
rem ===========================================================================
rem  下面这行把控制台切到 UTF-8，否则 cmd.exe（代码页 936）会把中文注释读成乱码
chcp 65001 >nul

rem  用法：
rem      run.cmd                      使用 config.toml 里的默认范围抓取
rem      run.cmd --rebuild-only       只用已缓存的 pages/ 重新生成章节（不联网）
rem      run.cmd --dry-run            只打印生效配置与抓取计划，不启动浏览器
rem      run.cmd --start 1 --end 20 --headless
rem      run.cmd --help               查看全部参数

setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
    echo [warn] 未找到 .venv，回退到系统 python（建议先创建虚拟环境）
)

"%PY%" -m douban_reader %*
exit /b %ERRORLEVEL%
