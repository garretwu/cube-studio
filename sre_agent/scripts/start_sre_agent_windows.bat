@echo off
setlocal

rem Windows launcher for local sre_agent frontend + backend.
rem Usage:
rem   sre_agent\scripts\start_sre_agent_windows.bat
rem   sre_agent\scripts\start_sre_agent_windows.bat --backend-port 18000 --frontend-port 18080

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "REPO_ROOT=%%~fI"

set "DEFAULT_CONFIG=sre_agent\conf\config.yaml"
set "PRIMARY_SESSION_DIR=%REPO_ROOT%\data\sessions"
set "LEGACY_SESSION_DIR=%REPO_ROOT%\data\session"

if exist "%REPO_ROOT%\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%REPO_ROOT%\.venv\Scripts\python.exe"
) else (
    set "PYTHON_EXE=python"
)

call :reset_dir "%PRIMARY_SESSION_DIR%"
if errorlevel 1 exit /b %errorlevel%

if exist "%LEGACY_SESSION_DIR%" (
    call :reset_dir "%LEGACY_SESSION_DIR%"
    if errorlevel 1 exit /b %errorlevel%
)

echo [info] repo root: "%REPO_ROOT%"
echo [info] using python: "%PYTHON_EXE%"
echo [info] starting sre_agent backend and frontend...

pushd "%REPO_ROOT%"
"%PYTHON_EXE%" "sre_agent\scripts\start_frontend_backend.py" --config "%DEFAULT_CONFIG%" %*
set "EXIT_CODE=%ERRORLEVEL%"
popd

exit /b %EXIT_CODE%

:reset_dir
set "TARGET_DIR=%~1"
if not defined TARGET_DIR (
    echo [error] target directory is empty
    exit /b 1
)

if /I "%TARGET_DIR%"=="%REPO_ROOT%" (
    echo [error] refusing to clear repo root: "%TARGET_DIR%"
    exit /b 1
)

if /I "%TARGET_DIR%"=="%REPO_ROOT%\data" (
    echo [error] refusing to clear data root: "%TARGET_DIR%"
    exit /b 1
)

if exist "%TARGET_DIR%" (
    echo [clean] removing "%TARGET_DIR%"
    rmdir /s /q "%TARGET_DIR%"
)

mkdir "%TARGET_DIR%" >nul 2>&1
if errorlevel 1 (
    echo [error] failed to recreate "%TARGET_DIR%"
    exit /b 1
)

echo [clean] ready "%TARGET_DIR%"
exit /b 0
