@echo off
setlocal
title GPGest - Iniciar servidor

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Iniciar.ps1"
set "gpgestExitCode=%ERRORLEVEL%"

if not "%gpgestExitCode%"=="0" (
    echo.
    echo Nao foi possivel iniciar o GPGest. Consulte a mensagem acima.
    pause
    exit /b %gpgestExitCode%
)

echo.
echo Servidor iniciado. Abra http://127.0.0.1:5174
pause
exit /b 0
