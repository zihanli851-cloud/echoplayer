param(
    [int]$Port = 5000,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $ProjectRoot

$env:PYTHONDONTWRITEBYTECODE = "1"
$env:PYTHONUTF8 = "1"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Get-PythonCommand {
    $venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return $py.Source
    }

    throw "Python was not found. Please install Python 3.12+ first."
}

function Test-PortFree {
    param([int]$PortToCheck)
    $connection = Get-NetTCPConnection -LocalPort $PortToCheck -State Listen -ErrorAction SilentlyContinue
    return $null -eq $connection
}

function Get-FreePort {
    param([int]$StartPort)
    for ($candidate = $StartPort; $candidate -le ($StartPort + 20); $candidate++) {
        if (Test-PortFree $candidate) {
            return $candidate
        }
    }
    throw "No free port found from $StartPort to $($StartPort + 20)."
}

function Ensure-Pip {
    param([string]$PythonPath)

    Write-Step "Preparing pip"
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $PythonPath -m ensurepip --upgrade
        $ensurePipExitCode = $LASTEXITCODE
        & $PythonPath -m pip --version *> $null
        $pipExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    if ($ensurePipExitCode -ne 0 -or $pipExitCode -ne 0) {
        throw "Failed to prepare pip in .venv. Delete .venv and run start.bat again."
    }
}

$python = Get-PythonCommand
$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Step "Creating virtual environment"
    & $python -m venv ".venv"
    $python = $venvPython
} else {
    $python = $venvPython
}

Ensure-Pip $python

Write-Step "Checking dependencies"
$dependencyCheck = & $python -c "import importlib.util; modules=['fastapi','uvicorn','jinja2','pdfplumber','rapidfuzz']; missing=[name for name in modules if importlib.util.find_spec(name) is None]; print(','.join(missing))"
if ($dependencyCheck.Trim()) {
    Write-Step "Installing dependencies"
    & $python -m pip install -r "requirements.txt"
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed."
    }
}

$selectedPort = Get-FreePort $Port
$url = "http://127.0.0.1:$selectedPort"

Write-Step "Starting EchoPaper"
Write-Host "Project: $ProjectRoot"
Write-Host "URL:     $url"
Write-Host ""
Write-Host "Press Ctrl+C to stop the server." -ForegroundColor Yellow
Write-Host ""

if (-not $NoOpen) {
    Start-Job -ScriptBlock {
        param([string]$TargetUrl)
        Start-Sleep -Seconds 2
        Start-Process $TargetUrl
    } -ArgumentList $url | Out-Null
}

& $python -m uvicorn app.main:app --reload --host 127.0.0.1 --port $selectedPort
if ($LASTEXITCODE -ne 0) {
    throw "EchoPaper server exited before it was ready."
}
