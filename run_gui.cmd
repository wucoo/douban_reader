@echo off
rem ===========================================================================
rem  douban-reader GUI launcher  -  just double-click this file
rem
rem  ASCII only + CRLF + no "chcp": see tests/test_assets.py for the reason.
rem  Uses pythonw.exe so no black console window is left behind.
rem
rem  If double-clicking seems to do nothing:
rem    1) run this command in a console to see the error:
rem         .venv\Scripts\python.exe -m douban_reader.gui
rem    2) or look for logs\gui-error-*.log
rem ===========================================================================
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    set "PY=.venv\Scripts\pythonw.exe"
) else (
    set "PY=pythonw"
)

start "" "%PY%" -m douban_reader.gui
exit /b 0
