@echo off
rem ============================================================
rem  Tavily Key Pool - Windows one-click build script
rem
rem  Main output: out\dist\Tavily\  (onedir app folder)
rem    - out\dist\Tavily\Tavily.exe : main launcher (windowed)
rem    - out\dist\Tavily\_internal\ : bundled libs/data (PyInstaller 6)
rem    - Runtime data (config/db/logs) is written to a data\ folder next
rem      to the exe (out\dist\Tavily\data) and preserved across rebuilds.
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

rem -- Stop any running Tavily.exe (a running instance locks the output exe
rem    so PyInstaller cannot overwrite it; the app must be closed to rebuild).
taskkill /IM Tavily.exe /F >nul 2>nul
rem -- Wait briefly so the killed process releases its file handles.
ping -n 3 127.0.0.1 >nul 2>nul
rem -- Fresh work dir: out\build (PyInstaller intermediate files).
rem    NOTE: PyInstaller onedir rebuild wipes out\dist\Tavily entirely,
rem    so its data\ (config/db/logs) is backed up first and restored after.
rd /s /q "out\build" >nul 2>nul
if not exist "out\dist" mkdir "out\dist" >nul 2>nul
set "APP_DIR=out\dist\Tavily"
set "DATA_BACKUP=out\dist\.data-backup"
if exist "%APP_DIR%\data" (
  echo Preserving %APP_DIR%\data across rebuild ...
  rd /s /q "%DATA_BACKUP%" >nul 2>nul
  move "%APP_DIR%\data" "%DATA_BACKUP%" >nul 2>nul
)

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
echo [1/1] Building Tavily (onedir)...
rem Build from Tavily.spec (single source of truth: datas/binaries/hiddenimports).
rem onedir mode has NO %%TEMP%%\_MEI* unpack dir, so there is no
rem "Failed to remove temporary directory" popup on exit, and startup is faster.
%PY% -m PyInstaller --noconfirm --clean ^
  --workpath out\build ^
  --distpath out\dist ^
  Tavily.spec
if errorlevel 1 (
  echo.
  echo *** Build FAILED ***
  echo See the error messages above.
  echo If it says out\dist\Tavily is locked by another process,
  echo close the running Tavily app first, then run this script again.
  echo.
  goto :restore_data
)

echo.
echo === Build complete! ===
echo   Output: %cd%\out\dist\Tavily\
echo     - Tavily.exe  : double-click to open the native app window
echo     - _internal\  : bundled libraries/data (keep with the exe)
echo     - data\       : runtime config/db/logs (auto-created)
echo.
echo   Usage:
echo     - Double-click out\dist\Tavily\Tavily.exe - native app window (WebView2)
echo     - Panel "MCP" tab can start/stop the MCP service, URL auto-copied
echo     - Listens on 0.0.0.0 by default, LAN devices can access
echo.
echo   Notes:
echo     - Runtime data lives in a data\ folder next to the exe:
echo       data\config.json, data\tavily_keys.db, data\*.log, data\.tavily-secret.key.
echo     - Old config/db/logs left at the old locations are auto-migrated
echo       into data\ on first run (no data loss).
echo     - Distribute by zipping the whole out\dist\Tavily\ folder.
echo.
echo   (Optional) To point an AI client at this machine via stdio:
echo     Set the AI client MCP command to:  Tavily.exe --mcp
echo     And set mcp_transport to "stdio" in data\config.json
echo.
goto :restore_data

:restore_data
rem -- Restore preserved runtime data back next to the freshly built exe.
if exist "%DATA_BACKUP%" (
  if not exist "%APP_DIR%" mkdir "%APP_DIR%"
  move "%DATA_BACKUP%" "%APP_DIR%\data" >nul 2>nul
)
goto :end

:end
endlocal
pause >nul
exit /b 0
