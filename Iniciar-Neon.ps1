$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$gpgestRuntime = Join-Path $PSScriptRoot '.local'
$gpgestNeonEnvironment = Join-Path $gpgestRuntime 'neon-runtime.env'
if (!(Test-Path -LiteralPath $gpgestNeonEnvironment)) { throw 'Conclua primeiro a cópia para o Neon: consulte docs/NEON.md.' }
if (Get-NetTCPConnection -LocalPort 8001,5174 -State Listen -ErrorAction SilentlyContinue) { throw 'As portas 8001 ou 5174 já estão ocupadas. Execute Parar.ps1 antes de mudar de base.' }
& '.\.venv\Scripts\python.exe' scripts\migrate-neon.py ready
if ($LASTEXITCODE) { throw 'Não foi possível validar a ligação ao Neon. Nenhum servidor foi iniciado.' }
$gpgestPreviousEnvironment = $env:GPGEST_ENV_FILE
try {
    $env:GPGEST_ENV_FILE = $gpgestNeonEnvironment
    $gpgestBackend = Start-Process -FilePath (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8001') -WorkingDirectory (Join-Path $PSScriptRoot 'backend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $gpgestRuntime 'backend.log') -RedirectStandardError (Join-Path $gpgestRuntime 'backend-error.log') -PassThru
    @{ Id=$gpgestBackend.Id; Started=$gpgestBackend.StartTime.ToUniversalTime().Ticks } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $gpgestRuntime 'backend.pid')
    $gpgestFrontend = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c','npm.cmd run dev') -WorkingDirectory (Join-Path $PSScriptRoot 'frontend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $gpgestRuntime 'frontend.log') -RedirectStandardError (Join-Path $gpgestRuntime 'frontend-error.log') -PassThru
    @{ Id=$gpgestFrontend.Id; Started=$gpgestFrontend.StartTime.ToUniversalTime().Ticks } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $gpgestRuntime 'frontend.pid')
} finally {
    $env:GPGEST_ENV_FILE = $gpgestPreviousEnvironment
}
Write-Host 'GPGest com dados no Neon: http://127.0.0.1:5174 | API neste PC: http://127.0.0.1:8001/docs'
