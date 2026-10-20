param([switch]$Reiniciar, [string]$EnderecoIP)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$gpgestRuntime = Join-Path $PSScriptRoot '.local'
if (!(Test-Path -LiteralPath '.env')) { throw 'Execute primeiro Instalar.ps1.' }
if (!$EnderecoIP) {
    $gpgestInterface = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' } | Select-Object -First 1
    $EnderecoIP = ($gpgestInterface.IPv4Address | Select-Object -First 1).IPAddress
}
if (!$EnderecoIP -or !(Get-NetIPAddress -AddressFamily IPv4 -IPAddress $EnderecoIP -ErrorAction SilentlyContinue)) { throw 'Não foi encontrado um endereço IPv4 desta rede. Ligue o computador ao Wi-Fi ou indique -EnderecoIP.' }

function Stop-GPGestListener([int]$gpgestPort) {
    $gpgestListeners = @(Get-NetTCPConnection -LocalPort $gpgestPort -State Listen -ErrorAction SilentlyContinue)
    foreach ($gpgestListener in $gpgestListeners) {
        $gpgestProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($gpgestListener.OwningProcess)"
        if (!$gpgestProcess -or $gpgestProcess.CommandLine.IndexOf($PSScriptRoot, [StringComparison]::OrdinalIgnoreCase) -lt 0) { throw "A porta $gpgestPort está ocupada por outro processo. Nenhum processo desconhecido foi parado." }
        if (!$Reiniciar) { throw 'A aplicação já está iniciada. Execute .\Iniciar-Rede.ps1 -Reiniciar para mudar para o acesso pela rede.' }
        Stop-Process -Id $gpgestListener.OwningProcess
        Wait-Process -Id $gpgestListener.OwningProcess -Timeout 15 -ErrorAction SilentlyContinue
    }
}
Stop-GPGestListener 5174
Stop-GPGestListener 8001
if (!$env:GPGEST_ENV_FILE -and !(Get-NetTCPConnection -LocalPort 55432 -State Listen -ErrorAction SilentlyContinue)) {
    & '.\.venv\Scripts\python.exe' scripts\init-local.py
    if ($LASTEXITCODE) { throw 'Não foi possível iniciar PostgreSQL.' }
}
$gpgestBackend = Start-Process -FilePath (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') -ArgumentList @('scripts\run-lan-backend.py','--ip',$EnderecoIP) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $gpgestRuntime 'backend-rede.log') -RedirectStandardError (Join-Path $gpgestRuntime 'backend-rede-error.log') -PassThru
@{ Id=$gpgestBackend.Id; Started=$gpgestBackend.StartTime.ToUniversalTime().Ticks } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $gpgestRuntime 'backend.pid')
$gpgestReady = $false
for ($gpgestAttempt=0; $gpgestAttempt -lt 30; $gpgestAttempt++) {
    try { $gpgestResponse = Invoke-WebRequest -Uri 'http://127.0.0.1:8001/openapi.json' -UseBasicParsing -TimeoutSec 2; if ($gpgestResponse.StatusCode -eq 200) { $gpgestReady=$true; break } } catch { }
    Start-Sleep -Milliseconds 500
}
if (!$gpgestReady) { throw 'A API não iniciou. Consulte .local/backend-rede-error.log; o perfil poderá exigir HTTPS.' }
$gpgestVite = Join-Path $PSScriptRoot 'frontend\node_modules\vite\bin\vite.js'
$gpgestFrontend = Start-Process -FilePath (Get-Command node.exe).Source -ArgumentList @(('"{0}"' -f $gpgestVite),'--host','0.0.0.0') -WorkingDirectory (Join-Path $PSScriptRoot 'frontend') -WindowStyle Hidden -RedirectStandardOutput (Join-Path $gpgestRuntime 'frontend-rede.log') -RedirectStandardError (Join-Path $gpgestRuntime 'frontend-rede-error.log') -PassThru
@{ Id=$gpgestFrontend.Id; Started=$gpgestFrontend.StartTime.ToUniversalTime().Ticks } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $gpgestRuntime 'frontend.pid')
Write-Host "No telemóvel ligado ao mesmo Wi-Fi: http://${EnderecoIP}:5174"
Write-Host "Administração: http://${EnderecoIP}:5174/plataforma"
Write-Host 'A API e o PostgreSQL permanecem em 127.0.0.1. Não é necessário abrir portas no router.'
