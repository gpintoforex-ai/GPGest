$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
function Stop-GPGestTree([int]$gpgestProcessId) {
    $gpgestChildren = Get-CimInstance Win32_Process -Filter "ParentProcessId=$gpgestProcessId"
    foreach ($gpgestChild in $gpgestChildren) { Stop-GPGestTree $gpgestChild.ProcessId }
    Stop-Process -Id $gpgestProcessId -ErrorAction SilentlyContinue
}
foreach ($gpgestName in @('backend','frontend')) {
    $gpgestPidFile = Join-Path $PSScriptRoot ".local\$gpgestName.pid"
    if (Test-Path -LiteralPath $gpgestPidFile) {
        $gpgestRecorded = Get-Content -LiteralPath $gpgestPidFile -Raw | ConvertFrom-Json
        $gpgestProcess = Get-Process -Id $gpgestRecorded.Id -ErrorAction SilentlyContinue
        if ($gpgestProcess -and $gpgestProcess.StartTime.ToUniversalTime().Ticks -eq $gpgestRecorded.Started) { Stop-GPGestTree $gpgestProcess.Id }
    }
}
$gpgestPgCtl = Join-Path $PSScriptRoot '.local\pgsql\bin\pg_ctl.exe'
if (Test-Path -LiteralPath $gpgestPgCtl) { & $gpgestPgCtl -D (Join-Path $PSScriptRoot '.local\pgdata') stop -m fast }
Write-Host 'Processos locais do GPGest parados. Os dados foram preservados.'
