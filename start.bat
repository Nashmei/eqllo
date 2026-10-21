@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Run install.bat first.
  pause
  exit /b 1
)
if not exist .env (
  echo Missing .env. Copy .env.example to .env and configure it.
  pause
  exit /b 1
)
call .venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
pause
