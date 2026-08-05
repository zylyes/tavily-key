@echo off
rem Tavily Key Pool - Windows local Dashboard launcher
rem Reads host/port from data/config.json; opens the app window (WebView2)
cd /d "%~dp0..\.."

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

echo Starting Tavily Dashboard (data/config.json) ...
%PY% app\dashboard.py
pause
