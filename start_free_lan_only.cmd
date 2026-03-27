@echo off
set SCRIPT_DIR=%~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start_local_ai.ps1" -FreeFallbackOnly
if errorlevel 1 (
    echo.
    echo Free LAN startup failed. Review the error above and press any key.
    pause >nul
)