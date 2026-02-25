#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Install NadirClaw and SurrealDB as Windows services using NSSM.

.DESCRIPTION
    This script:
    1. Downloads NSSM (Non-Sucking Service Manager) if not already present
    2. Installs both NadirClaw-SurrealDB and NadirClaw-Router as Windows services
    3. Configures auto-start, log rotation, and crash restart

    Both services will start automatically on Windows boot.
    NadirClaw-Router depends on NadirClaw-SurrealDB (starts after DB is ready).

.NOTES
    Must be run as Administrator.
    Run: powershell -ExecutionPolicy Bypass -File scripts\windows_service.ps1
#>

$ErrorActionPreference = "Stop"

$NadirClawDir = Join-Path $env:USERPROFILE ".nadirclaw"
$BinDir = Join-Path $NadirClawDir "bin"
$LogDir = Join-Path $NadirClawDir "logs"
$NssmExe = Join-Path $BinDir "nssm.exe"
$NssmUrl = "https://nssm.cc/release/nssm-2.24.zip"
$NssmZip = Join-Path $env:TEMP "nssm-2.24.zip"
$NssmExtract = Join-Path $env:TEMP "nssm-2.24"

# ---------------------------------------------------------------------------
# Ensure directories exist
# ---------------------------------------------------------------------------

foreach ($dir in @($NadirClawDir, $BinDir, $LogDir)) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        Write-Host "Created: $dir"
    }
}

# ---------------------------------------------------------------------------
# Download and extract NSSM if needed
# ---------------------------------------------------------------------------

if (-not (Test-Path $NssmExe)) {
    Write-Host "Downloading NSSM..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $NssmUrl -OutFile $NssmZip -UseBasicParsing

    Write-Host "Extracting NSSM..."
    Expand-Archive -Path $NssmZip -DestinationPath $env:TEMP -Force

    # Copy the 64-bit exe
    $NssmSrc = Join-Path $NssmExtract "win64" "nssm.exe"
    if (-not (Test-Path $NssmSrc)) {
        # Try alternate path structure
        $NssmSrc = Get-ChildItem -Path $env:TEMP -Filter "nssm.exe" -Recurse |
            Where-Object { $_.DirectoryName -match "win64" } |
            Select-Object -First 1 -ExpandProperty FullName
    }

    if (-not $NssmSrc -or -not (Test-Path $NssmSrc)) {
        Write-Error "Could not find nssm.exe in download. Please install NSSM manually."
        exit 1
    }

    Copy-Item $NssmSrc $NssmExe -Force
    Write-Host "NSSM installed: $NssmExe"

    # Cleanup
    Remove-Item $NssmZip -Force -ErrorAction SilentlyContinue
    Remove-Item $NssmExtract -Recurse -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "NSSM already installed: $NssmExe"
}

# ---------------------------------------------------------------------------
# Install services via NadirClaw CLI
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "Installing NadirClaw services..."
Write-Host ""

# Find the NadirClaw command
$NadirClawCmd = Get-Command nadirclaw -ErrorAction SilentlyContinue
if (-not $NadirClawCmd) {
    # Try the venv
    $VenvNadirClaw = Join-Path $NadirClawDir "venv" "Scripts" "nadirclaw.exe"
    if (Test-Path $VenvNadirClaw) {
        $NadirClawCmd = $VenvNadirClaw
    } else {
        Write-Host "NadirClaw not found. Install it first: pip install nadirclaw"
        Write-Host "Falling back to direct NSSM installation..."

        # Direct NSSM installation as fallback
        # Find SurrealDB
        $SurrealExe = Join-Path $env:LOCALAPPDATA "SurrealDB" "surreal.exe"
        if (-not (Test-Path $SurrealExe)) {
            $SurrealExe = (Get-Command surreal -ErrorAction SilentlyContinue).Source
        }
        if (-not $SurrealExe) {
            Write-Error "SurrealDB not found. Install it from https://surrealdb.com"
            exit 1
        }

        # Find Python
        $PythonExe = Join-Path $NadirClawDir "venv" "Scripts" "python.exe"
        if (-not (Test-Path $PythonExe)) {
            $PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
        }

        $DataDir = (Join-Path $NadirClawDir "surrealdb-data").Replace("\", "/")

        # Install SurrealDB service
        Write-Host "Installing NadirClaw-SurrealDB..."
        & $NssmExe install NadirClaw-SurrealDB $SurrealExe "start --log info --user root --pass root --bind 0.0.0.0:8000 file://$DataDir"
        & $NssmExe set NadirClaw-SurrealDB Description "NadirClaw SurrealDB instance"
        & $NssmExe set NadirClaw-SurrealDB AppStdout (Join-Path $LogDir "NadirClaw-SurrealDB.log")
        & $NssmExe set NadirClaw-SurrealDB AppStderr (Join-Path $LogDir "NadirClaw-SurrealDB-error.log")
        & $NssmExe set NadirClaw-SurrealDB AppRotateFiles 1
        & $NssmExe set NadirClaw-SurrealDB AppRotateBytes 10485760
        & $NssmExe set NadirClaw-SurrealDB AppRestartDelay 5000
        & $NssmExe set NadirClaw-SurrealDB Start SERVICE_AUTO_START
        & $NssmExe set NadirClaw-SurrealDB AppDirectory $NadirClawDir

        # Install NadirClaw Router service
        Write-Host "Installing NadirClaw-Router..."
        & $NssmExe install NadirClaw-Router $PythonExe "-m nadirclaw.server"
        & $NssmExe set NadirClaw-Router Description "NadirClaw LLM Router"
        & $NssmExe set NadirClaw-Router AppStdout (Join-Path $LogDir "NadirClaw-Router.log")
        & $NssmExe set NadirClaw-Router AppStderr (Join-Path $LogDir "NadirClaw-Router-error.log")
        & $NssmExe set NadirClaw-Router AppRotateFiles 1
        & $NssmExe set NadirClaw-Router AppRotateBytes 10485760
        & $NssmExe set NadirClaw-Router AppRestartDelay 5000
        & $NssmExe set NadirClaw-Router Start SERVICE_AUTO_START
        & $NssmExe set NadirClaw-Router AppDirectory $NadirClawDir
        & $NssmExe set NadirClaw-Router DependOnService NadirClaw-SurrealDB

        Write-Host ""
        Write-Host "Services installed. Starting..."
        & $NssmExe start NadirClaw-SurrealDB
        Start-Sleep -Seconds 3
        & $NssmExe start NadirClaw-Router

        Write-Host ""
        Write-Host "Done! Check status with:"
        Write-Host "  nssm status NadirClaw-SurrealDB"
        Write-Host "  nssm status NadirClaw-Router"
        exit 0
    }
}

# Use the NadirClaw CLI
& $NadirClawCmd service install
if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    & $NadirClawCmd service start
    Write-Host ""
    & $NadirClawCmd service status
} else {
    Write-Error "Service installation failed."
    exit 1
}

Write-Host ""
Write-Host "Setup complete! Services will auto-start on boot."
Write-Host "Manage with: nadirclaw service [start|stop|status|logs]"
