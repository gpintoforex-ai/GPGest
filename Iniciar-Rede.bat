@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Iniciar-Rede.ps1" -Reiniciar
if errorlevel 1 pause
