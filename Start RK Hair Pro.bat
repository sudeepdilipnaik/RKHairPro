@echo off
title RK Hair Pro - local server
cd /d "%~dp0"
echo.
echo   Starting RK Hair Pro ...  (first start creates the demo data and can take 1-2 minutes)
echo   When you see "RK Hair Pro is running", open:  http://127.0.0.1:5000
echo.
start "" "http://127.0.0.1:5000"
python run.py
pause
