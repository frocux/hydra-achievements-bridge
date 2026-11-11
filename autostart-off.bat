@echo off
rem Turns autostart OFF and stops the running watcher.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0autostart-off.ps1"
echo.
pause
