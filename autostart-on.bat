@echo off
rem Turns autostart ON: quiet pythonw watcher, every 60 seconds.
rem All user-facing text is printed by autostart-on.ps1 (Russian).
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0autostart-on.ps1"
echo.
pause
