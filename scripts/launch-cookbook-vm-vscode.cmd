@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PS_SCRIPT=%SCRIPT_DIR%launch-cookbook-vm-vscode.ps1"
set "PWSH="

rem Prefer pwsh from PATH.
where pwsh >nul 2>&1
if not errorlevel 1 set "PWSH=pwsh"

rem Fallback to common PowerShell 7 install locations.
if not defined PWSH if exist "%ProgramFiles%\PowerShell\7\pwsh.exe" set "PWSH=%ProgramFiles%\PowerShell\7\pwsh.exe"
if not defined PWSH if exist "%ProgramFiles%\PowerShell\7-preview\pwsh.exe" set "PWSH=%ProgramFiles%\PowerShell\7-preview\pwsh.exe"
if not defined PWSH if exist "%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe" set "PWSH=%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe"

if not defined PWSH (
  echo PowerShell 7 ^(pwsh^) was not found.
  echo Install it from https://aka.ms/powershell and ensure pwsh is on PATH.
  echo.
  pause
  exit /b 1
)

"%PWSH%" -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" %*
set "EXITCODE=%ERRORLEVEL%"
if not "%EXITCODE%"=="0" (
  echo.
  echo Launcher failed with exit code %EXITCODE%.
  pause
)

endlocal