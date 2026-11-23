@echo off
title EduBridge
cd /d "%~dp0"
set PYTHONUNBUFFERED=1
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" edubridge.py
) else (
  python edubridge.py
)
if errorlevel 1 (
  echo.
  echo EduBridge stopped because of an error.
  pause
)
