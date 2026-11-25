@echo off
setlocal
cd /d "%~dp0"
if not exist .venv (
  echo Creating virtual environment...
  uv venv .venv || python -m venv .venv
  uv pip install --python .venv -r requirements.txt || .venv\Scripts\python -m pip install -r requirements.txt
)
if not exist .env copy .env.example .env >nul
if not exist models\llm (
  echo.
  echo Offline models not found. Downloading ^(~5.5 GB, one time^)...
  .venv\Scripts\python download_models.py
)
start "" http://127.0.0.1:8000
.venv\Scripts\python app.py
