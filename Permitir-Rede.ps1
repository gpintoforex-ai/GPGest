# Execute como administrador apenas se a Firewall do Windows impedir o acesso pelo Wi-Fi.
$ErrorActionPreference = 'Stop'
$gpgestPrincipal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (!$gpgestPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Execute este ficheiro num PowerShell como administrador para criar a regra da Firewall do Windows.' }
$gpgestNode = (Get-Command node.exe).Source
if (!(Get-NetFirewallRule -Name 'GPGest-LAN-5174' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name 'GPGest-LAN-5174' -DisplayName 'GPGest - acesso na rede local (5174)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5174 -RemoteAddress LocalSubnet -Profile Private,Public -Program $gpgestNode | Out-Null
}
Write-Host 'Acesso à porta 5174 permitido apenas a dispositivos da rede local.'
