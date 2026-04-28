# install-reliability-patch.ps1 — run ONCE in elevated PowerShell.
#
# Idempotent. Re-running is safe (re-applies settings).
#
# What it does:
#   1. Kills the bare next-start process holding port 3000
#   2. Creates an NSSM service "AkDashboard" that owns port 3000
#      (auto-start on boot, auto-restart on crash, log rotation)
#   3. Restarts NadirClawSentinel so it picks up the patched
#      sentinel.py (now monitors cloudflared + status_app)

$ErrorActionPreference = "Stop"

$Nssm    = "C:\Users\Agile\AppData\Local\Microsoft\WinGet\Links\nssm.exe"
$Node    = "C:\Program Files\Volta\node.exe"
$DashDir = "C:\Users\Agile\ak_dashboard"

if (-not (Test-Path $Nssm)) { throw "NSSM not found at $Nssm" }
if (-not (Test-Path $Node)) { throw "Node not found at $Node" }
if (-not (Test-Path "$DashDir\node_modules\next\dist\bin\next")) { throw "Next CLI not present in $DashDir — run npm install" }

# 1. Free port 3000 from the bare process so the service can bind it.
Write-Host "[1/3] Freeing port 3000..." -ForegroundColor Cyan
$conns = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
foreach ($c in $conns) {
    Write-Host "  killing PID $($c.OwningProcess)"
    Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

# 2. Install or update the AkDashboard service.
Write-Host "[2/3] Installing AkDashboard NSSM service..." -ForegroundColor Cyan
$existing = Get-Service AkDashboard -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "  service exists — stopping for reconfigure"
    & $Nssm stop AkDashboard 2>&1 | Out-Null
    Start-Sleep -Seconds 2
} else {
    Write-Host "  installing fresh"
    & $Nssm install AkDashboard $Node "node_modules\next\dist\bin\next" "start" 2>&1 | Out-Null
}

& $Nssm set AkDashboard AppDirectory $DashDir 2>&1 | Out-Null
& $Nssm set AkDashboard AppStdout "$DashDir\service.log" 2>&1 | Out-Null
& $Nssm set AkDashboard AppStderr "$DashDir\service.log" 2>&1 | Out-Null
& $Nssm set AkDashboard AppRotateFiles 1 2>&1 | Out-Null
& $Nssm set AkDashboard AppRotateBytes 10485760 2>&1 | Out-Null
& $Nssm set AkDashboard AppEnvironmentExtra "NODE_ENV=production" "PORT=3000" 2>&1 | Out-Null
& $Nssm set AkDashboard Start SERVICE_AUTO_START 2>&1 | Out-Null
& $Nssm set AkDashboard AppExit Default Restart 2>&1 | Out-Null
& $Nssm set AkDashboard AppRestartDelay 5000 2>&1 | Out-Null
& $Nssm set AkDashboard AppThrottle 10000 2>&1 | Out-Null

& $Nssm start AkDashboard 2>&1 | Out-Null
Start-Sleep -Seconds 8

$svc = Get-Service AkDashboard
Write-Host "  AkDashboard: $($svc.Status)" -ForegroundColor Green

$port = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
if ($port) {
    Write-Host "  port 3000 owner PID: $($port.OwningProcess)" -ForegroundColor Green
} else {
    Write-Host "  WARNING: port 3000 not bound yet — check $DashDir\service.log" -ForegroundColor Yellow
}

# 3. Restart NadirClawSentinel to load the patched sentinel.py
Write-Host "[3/3] Restarting NadirClawSentinel (loads cloudflared + status_app monitors)..." -ForegroundColor Cyan
Restart-Service NadirClawSentinel -Force
Start-Sleep -Seconds 3
$sent = Get-Service NadirClawSentinel
Write-Host "  NadirClawSentinel: $($sent.Status)" -ForegroundColor Green

Write-Host ""
Write-Host "Done. Sentinel will now monitor:" -ForegroundColor Green
Write-Host "  - nadirclaw    (8856)"
Write-Host "  - surrealdb    (8000)"
Write-Host "  - ak_dashboard (3000)  <- now an auto-restarting NSSM service"
Write-Host "  - ollama       (11434)"
Write-Host "  - status_app   (8766)  <- newly monitored"
Write-Host "  - cloudflared  (deep edge-connection check)  <- newly monitored"
Write-Host ""
Write-Host "Next time anything dies (sleep, reboot, crash, tunnel disconnect):"
Write-Host "  - NSSM restarts the process within seconds"
Write-Host "  - Sentinel double-checks within 60s and restarts if still down"
Write-Host "  - You get a Telegram notification"
Write-Host ""
Read-Host "Press Enter to close"
