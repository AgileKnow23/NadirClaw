# fix-akdashboard.ps1 -- patches the broken AppDirectory on AkDashboard,
# starts the service, and reloads NadirClawSentinel so it picks up the
# patched sentinel.py. Errors NOT swallowed this time.

$ErrorActionPreference = "Continue"

$Nssm    = "C:\Users\Agile\AppData\Local\Microsoft\WinGet\Links\nssm.exe"
$DashDir = "C:\Users\Agile\ak_dashboard"

Write-Host "[1/5] Stopping AkDashboard if running..." -ForegroundColor Cyan
& $Nssm stop AkDashboard
Start-Sleep -Seconds 3

Write-Host "[2/5] Freeing port 3000 from any squatter..." -ForegroundColor Cyan
$conns = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
foreach ($c in $conns) {
    Write-Host "  killing PID $($c.OwningProcess)"
    Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

Write-Host "[3/5] Setting AppDirectory + reapplying full config..." -ForegroundColor Cyan
& $Nssm set AkDashboard AppDirectory $DashDir
& $Nssm set AkDashboard AppStdout "$DashDir\service.log"
& $Nssm set AkDashboard AppStderr "$DashDir\service.log"
& $Nssm set AkDashboard AppRotateFiles 1
& $Nssm set AkDashboard AppRotateBytes 10485760
& $Nssm set AkDashboard AppEnvironmentExtra "NODE_ENV=production" "PORT=3000"
& $Nssm set AkDashboard Start SERVICE_AUTO_START
& $Nssm set AkDashboard AppExit Default Restart
& $Nssm set AkDashboard AppRestartDelay 5000
& $Nssm set AkDashboard AppThrottle 10000

Write-Host ""
Write-Host "  AppDirectory readback:" -ForegroundColor Yellow
& $Nssm get AkDashboard AppDirectory
Write-Host "  Application readback:" -ForegroundColor Yellow
& $Nssm get AkDashboard Application
Write-Host "  AppParameters readback:" -ForegroundColor Yellow
& $Nssm get AkDashboard AppParameters

Write-Host ""
Write-Host "[4/5] Starting AkDashboard..." -ForegroundColor Cyan
& $Nssm start AkDashboard
Start-Sleep -Seconds 12

$svc = Get-Service AkDashboard
Write-Host "  Service status: $($svc.Status)" -ForegroundColor Green

$port = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
if ($port) {
    Write-Host "  port 3000 owner: PID $($port.OwningProcess)" -ForegroundColor Green
} else {
    Write-Host "  WARNING: port 3000 still empty -- check $DashDir\service.log" -ForegroundColor Yellow
    if (Test-Path "$DashDir\service.log") {
        Write-Host "--- last 15 lines of service.log ---" -ForegroundColor Yellow
        Get-Content "$DashDir\service.log" -Tail 15
    }
}

Write-Host ""
Write-Host "[5/5] Restarting NadirClawSentinel (loads cloudflared + status_app monitors)..." -ForegroundColor Cyan
Restart-Service NadirClawSentinel -Force
Start-Sleep -Seconds 5
$sent = Get-Service NadirClawSentinel
Write-Host "  Sentinel status: $($sent.Status)" -ForegroundColor Green

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Read-Host "Press Enter to close"
