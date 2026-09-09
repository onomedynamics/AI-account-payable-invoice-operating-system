@echo off
cd /d "%~dp0"
echo Running diagnostics... this takes about 30 seconds.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0diagnose.ps1" > "%~dp0diagnose.log" 2>&1
echo.
echo Done. Created: %~dp0diagnose.log
echo Tell Claude "done" and it will read the file.
pause
