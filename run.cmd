@echo off
rem ===========================================================================
rem  douban-reader launcher
rem
rem  IMPORTANT - keep this file ASCII-only, CRLF, no BOM, and never add "chcp".
rem  ---------------------------------------------------------------------------
rem  cmd.exe parses batch files by byte offset.  Any multi-byte character
rem  (Chinese comments) - and especially a mid-file "chcp" that changes the code
rem  page while the file is being read - desynchronises that offset, so cmd
rem  starts executing fragments of the comment lines as commands.  The symptom
rem  is a couple of "not recognized as an internal or external command" errors
rem  printed before the real output.
rem
rem  Chinese usage text therefore lives in `run.cmd --help` and README.md,
rem  not in this file.  tests/test_assets.py enforces all three rules.
rem
rem  Usage:
rem    run.cmd                                        scrape using config.toml
rem    run.cmd --rebuild-only                         rebuild chapters from pages/ (offline)
rem    run.cmd --dry-run                              print resolved settings and plan
rem    run.cmd --ebook-id 1465780 --start 1 --end 7    scrape pages 1-7 of another book
rem    run.cmd --help                                 full option list (Chinese)
rem ===========================================================================
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
    echo [warn] .venv not found, falling back to system python
)

"%PY%" -m douban_reader %*
exit /b %ERRORLEVEL%
