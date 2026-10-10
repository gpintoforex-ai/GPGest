$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (!(Test-Path -LiteralPath '.venv\Scripts\python.exe')) { py -m venv .venv; if ($LASTEXITCODE) { throw 'Falha ao criar ambiente Python.' } }
& '.\.venv\Scripts\python.exe' -m pip install -r backend\requirements.txt
if ($LASTEXITCODE) { throw 'Falha ao instalar dependências Python.' }
& '.\.venv\Scripts\python.exe' scripts\init-local.py
if ($LASTEXITCODE) { throw 'Falha ao preparar PostgreSQL.' }
Push-Location backend
try {
    & '..\.venv\Scripts\alembic.exe' upgrade head
    if ($LASTEXITCODE) { throw 'Falha nas migrações.' }
    & '..\.venv\Scripts\python.exe' seed.py
    if ($LASTEXITCODE) { throw 'Falha ao inicializar dados.' }
} finally { Pop-Location }
Push-Location frontend
try { npm.cmd ci; if ($LASTEXITCODE) { throw 'Falha ao instalar frontend.' } } finally { Pop-Location }
Write-Host 'Instalação concluída. Execute .\Iniciar.ps1. Consulte .local\ACESSO-LOCAL.txt para entrar.'
