# Lance l'interface web BACNSO
# Usage : clic droit -> Executer avec PowerShell, ou : .\start-web.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Environnement virtuel absent. Creation..." -ForegroundColor Yellow
    python -m venv (Join-Path $root ".venv")
    & $py -m pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org -r (Join-Path $root "requirements.txt")
}
Write-Host "Interface BACNSO -> http://127.0.0.1:5000" -ForegroundColor Cyan
Start-Process "http://127.0.0.1:5000"
& $py (Join-Path $root "webapp.py")
