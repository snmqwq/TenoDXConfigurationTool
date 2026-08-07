@echo off
setlocal
fltmc.exe >nul 2>&1
if errorlevel 1 (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_dfu_driver.ps1"
set "driver_exit=%ERRORLEVEL%"
echo.
pause
exit /b %driver_exit%
