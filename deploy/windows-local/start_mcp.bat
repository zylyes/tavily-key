@echo off
rem Tavily Key Pool - Windows local MCP Server launcher (stdio)
cd /d "%~dp0..\.."

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

echo Starting Tavily MCP Server ...
%PY% app\mcp_server.py
pause
