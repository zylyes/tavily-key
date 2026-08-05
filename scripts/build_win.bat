@echo off
rem ============================================================
rem  Tavily Key Pool - Windows one-click build script
rem
rem  Main output: dist\Tavily.exe (single-file app, no install)
rem    - Double-click -> native app window (WebView2 wrapper)
rem    - Panel has MCP service on/off, settings, auto-copy URL
rem    - Panel spawns "Tavily.exe --mcp" as child process
rem      (SSE / Streamable HTTP, listens on 0.0.0.0 by default)
rem ============================================================
setlocal enabledelayedexpansion
rem -- Script lives in scripts/; project root is one level up
cd /d "%~dp0.."

echo.
echo === Tavily Key Pool Windows build ===
echo.

rem -- Stop any running Tavily.exe (a running instance locks dist\Tavily.exe
rem    so PyInstaller cannot overwrite it; the app must be closed to rebuild).
taskkill /IM Tavily.exe /F >nul 2>nul
rem -- Wait briefly so the killed process releases its file handles.
ping -n 3 127.0.0.1 >nul 2>nul
rem -- Remove a stale temp file left by an earlier aborted rename, if any.
del "dist\Tavily.old.tmp" >nul 2>nul

rem -- Pick Python (prefer venv; MCP deps only in venv) --------
set "PY=python"
if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
  echo Using venv: .venv\Scripts\python.exe
) else (
  echo Using system Python: %PY%
  echo WARNING: .venv not found. MCP build may fail.
  echo          Run: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
)

rem -- Ensure PyInstaller is installed --------------------------
%PY% -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
  echo PyInstaller not found, installing ...
  %PY% -m pip install pyinstaller
)

echo.
echo [1/1] Building Tavily.exe (Dashboard + MCP service)...
rem Note: do NOT use --collect-all mcp (mcp.cli exits via sys.exit on import).
rem       Collect the core subpackages explicitly instead.
%PY% -m PyInstaller --noconfirm --clean --onefile --noconsole ^
  --name Tavily ^
  --icon assets\tavily.ico ^
  --add-data "app\dashboard.html;." ^
  --add-data "assets\tavily.ico;." ^
  --paths app ^
  --collect-all tavily ^
  --collect-all webview ^
  --hidden-import mcp_server ^
  --hidden-import uvicorn.loops.auto ^
  --hidden-import uvicorn.loops.asyncio ^
  --hidden-import uvicorn.protocols.http.auto ^
  --hidden-import uvicorn.protocols.http.h11_impl ^
  --hidden-import uvicorn.protocols.websockets.auto ^
  --hidden-import uvicorn.lifespan.on ^
  --collect-submodules mcp.server ^
  --collect-submodules mcp.shared ^
  --collect-submodules mcp.transport ^
  --collect-submodules mcp.session ^
  app\dashboard.py
if errorlevel 1 (
  echo.
  echo *** Build FAILED ***
  echo See the error messages above.
  echo If it says dist\Tavily.exe is being used by another process,
  echo close the running Tavily app first, then run this script again.
  echo.
  goto :end
)

echo.
echo === Build complete! ===
echo   Output: %cd%\dist\Tavily.exe
echo.
echo   Usage:
echo     - Double-click Tavily.exe - opens native app window (WebView2)
echo     - Panel "MCP" tab can start/stop the MCP service, URL auto-copied
echo     - Listens on 0.0.0.0 by default, LAN devices can access
echo.
echo   Notes:
echo     - First run auto-generates config.json and tavily_keys.db (next to exe).
echo     - MCP service log: mcp_server.log (next to exe).
echo.
echo   (Optional) To point an AI client at this machine via stdio:
echo     Set the AI client MCP command to:  Tavily.exe --mcp
echo     And set mcp_transport to "stdio" in config.json
echo.
goto :end

:end
endlocal
pause >nul
exit /b 0
