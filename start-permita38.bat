@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\permita38.exe" (
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 (echo Python 3 is required: https://www.python.org/downloads/ & exit /b 1)
  .venv\Scripts\python -m pip install --quiet --upgrade pip
  .venv\Scripts\python -m pip install --quiet -e .
)
.venv\Scripts\permita38.exe %*
