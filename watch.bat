@echo off
rem Watch mode: checks every 30 seconds while this window stays open.
chcp 65001 >nul
cd /d "%~dp0"
python bridge.py --watch 30
echo.
pause
