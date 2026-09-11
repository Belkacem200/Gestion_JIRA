# Lance l'interface web BACNSO (tableau de bord, planning, reporting...)
# Usage : clic droit -> "Executer avec PowerShell", ou depuis un terminal :
#   powershell -ExecutionPolicy Bypass -File .\start-web.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$port = 5000
$py = Join-Path $root ".venv\Scripts\python.exe"

# 1) Environnement virtuel : creation + dependances au premier lancement
if (-not (Test-Path $py)) {
    Write-Host "Environnement virtuel absent. Creation..." -ForegroundColor Yellow
    python -m venv (Join-Path $root ".venv")
    & $py -m pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r (Join-Path $root "requirements.txt")
}

# 2) Liberer le port si une instance tourne deja
$busy = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "Port $port deja utilise -> arret de l'instance precedente..." -ForegroundColor Yellow
    $busy | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
}

# 3) Ouvrir le navigateur puis lancer le serveur (Ctrl+C pour arreter)
Write-Host "Interface BACNSO -> http://127.0.0.1:$port" -ForegroundColor Cyan
Start-Process "http://127.0.0.1:$port"
Set-Location $root
& $py (Join-Path $root "webapp.py")
