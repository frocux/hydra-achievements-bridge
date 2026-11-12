@echo off
rem Runs the test suite: parsers, formats, encodings. Writes nothing outside a temp folder.
chcp 65001 >nul
cd /d "%~dp0"
python -m unittest discover -s tests -t . -v
echo.
pause
