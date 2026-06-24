@echo off
REM run.bat — Double-clickable batch file to execute PowerShell runner with bypass policy
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1"
pause
