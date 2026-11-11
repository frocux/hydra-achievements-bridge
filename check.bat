@echo off
rem Dry run: show what would be done, write nothing.
chcp 65001 >nul
cd /d "%~dp0"
python bridge.py --dry-run
echo.
pause
