@echo off
cd /d "%~dp0"
title invoice-ops server
echo.
echo   invoice-ops  ->  http://127.0.0.1:8000/docs
echo   Leave this window open. Press Ctrl+C or close it to stop.
echo.
".venv\Scripts\python.exe" -m uvicorn invoice_ops.api.main:app --port 8000
echo.
echo   (server stopped)
pause
