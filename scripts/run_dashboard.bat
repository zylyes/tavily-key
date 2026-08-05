@echo off
rem Start Tavily API Key Pool Dashboard (Windows)
rem Reads host/port from data/config.json; opens the app window (WebView2)
rem -- Script lives in scripts/; project root is one level up
cd /d "%~dp0.."

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

%PY% app\dashboard.py
pause
