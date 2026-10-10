$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (!(Test-Path -LiteralPath '.env')) { throw 'Execute primeiro .\Instalar.ps1.' }
& '.\.venv\Scripts\python.exe' scripts\init-local.py
if ($LASTEXITCODE) { throw 'Não foi possível iniciar PostgreSQL.' }
$gpgestRuntime = Join-Path $PSScriptRoot '.local'
if (Get-NetTCPConnection -LocalPort 8001,5174 -State Listen -ErrorAction SilentlyContinue) { throw 'As portas 8001 ou 5174 já estão ocupadas. Se o GPGest já está iniciado, abra http://127.0.0.1:5174. Para reiniciar, execute Parar.ps1 primeiro.' }
$gpgestBackend = Start-Process -FilePath (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8001') -WorkingDirectory (Join-Path $PSScriptRoot 'backend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $gpgestRuntime 'backend.log') -RedirectStandardError (Join-Path $gpgestRuntime 'backend-error.log') -PassThru
@{ Id=$gpgestBackend.Id; Started=$gpgestBackend.StartTime.ToUniversalTime().Ticks } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $gpgestRuntime 'backend.pid')
$gpgestFrontend = Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c','npm.cmd run dev') -WorkingDirectory (Join-Path $PSScriptRoot 'frontend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $gpgestRuntime 'frontend.log') -RedirectStandardError (Join-Path $gpgestRuntime 'frontend-error.log') -PassThru
@{ Id=$gpgestFrontend.Id; Started=$gpgestFrontend.StartTime.ToUniversalTime().Ticks } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $gpgestRuntime 'frontend.pid')
Write-Host 'GPGest: http://127.0.0.1:5174 | API: http://127.0.0.1:8001/docs'
