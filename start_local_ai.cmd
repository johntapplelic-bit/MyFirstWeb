@echo off
set SCRIPT_DIR=%~dp0

:: Check if already running as Administrator
net session >nul 2>&1
if %errorlevel% == 0 goto :run

:: Not elevated — relaunch self as Administrator automatically
echo Requesting administrator access to set up firewall rule for LAN access...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Process -FilePath 'cmd.exe' -ArgumentList '/c \"\"%~f0\"\"' -Verb RunAs -Wait"
exit /b

:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start_local_ai.ps1"
if errorlevel 1 (
    echo.
    echo Startup failed. Review the error above and press any key.
    pause >nul
)