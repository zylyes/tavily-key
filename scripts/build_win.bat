@echo off
rem ============================================================
rem  Tavily Key Pool - Windows one-click build script
rem
rem  Flow: [1/2] build web frontend (web\dist via npm ci + npm run build)
rem        [2/2] PyInstaller onedir (Tavily.spec bundles web\dist into
rem              _internal\web\dist; dashboard.py serves it at runtime.
rem              web\dist is REQUIRED - there is no legacy fallback)
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

rem -- [1/2] Build the web frontend (Vue 3 + Vite -> web\dist) --------
rem    Done FIRST, before touching any running instance or output data,
rem    so a missing toolchain fails fast without side effects.
where node >nul 2>nul
if errorlevel 1 (
  echo *** Node.js not found in PATH ***
  echo The dashboard frontend ^(web\^) requires Node.js to build.
  echo Install Node.js LTS from https://nodejs.org/ then rerun this script.
  goto :fail
)
where npm >nul 2>nul
if errorlevel 1 (
  echo *** npm not found in PATH ***
  echo The dashboard frontend ^(web\^) requires npm ^(ships with Node.js^).
  echo Install Node.js LTS from https://nodejs.org/ then rerun this script.
  goto :fail
)
echo [1/2] Building dashboard frontend ^(web\dist^)...
rem -- Stop any vite dev server running from THIS project: a lingering
rem    "npm run dev" keeps native modules (rollup .node / esbuild.exe) under
rem    web\node_modules locked, and "npm ci" then fails with EPERM unlink.
rem    Only node processes whose command line contains this project's web
rem    directory are killed; unrelated node processes are left alone.
set "WEBDIR=%CD%\web"
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"name='node.exe'\" | Where-Object { $_.CommandLine -like ('*' + $env:WEBDIR + '*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>nul
rem -- Kill lingering esbuild service processes left by previous builds:
rem    they lock node_modules\@esbuild\win32-x64\esbuild.exe and make
rem    "npm ci" fail with EPERM unlink on Windows.
taskkill /IM esbuild.exe /F >nul 2>nul
cd web
call npm ci
if errorlevel 1 (
  echo npm ci failed, retrying once after a short wait ...
  ping -n 3 127.0.0.1 >nul 2>nul
  call npm ci
)
if errorlevel 1 (
  echo.
  echo *** Frontend dependency install FAILED ^(npm ci^) ***
  echo If this is a network/registry error, try a mirror first:
  echo   npm config set registry https://registry.npmmirror.com
  goto :fail
)
call npm run build
if errorlevel 1 (
  echo.
  echo *** Frontend build FAILED ^(npm run build^) ***
  goto :fail
)
cd /d "%~dp0.."
if not exist "web\dist\index.html" (
  echo.
  echo *** Frontend build produced no web\dist\index.html ***
  goto :fail
)
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
echo [2/2] Building Tavily (onedir)...
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

:fail
rem -- Any failure lands here: keep the window open so the error is readable
rem    when the script is double-clicked (no more silent flash-exit).
echo.
echo ============================================================
echo  Build aborted. See the error above.
echo ============================================================
pause
endlocal
exit /b 1

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
