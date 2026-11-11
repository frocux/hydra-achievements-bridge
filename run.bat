@echo off
rem Runs the bridge now: collects achievements where Hydra can see them.
rem All user-facing text is printed by bridge.py itself (Russian).
chcp 65001 >nul
cd /d "%~dp0"
python bridge.py
echo.
pause
