# dashboard-health.ps1
# One-glance health check for Bryan's local stack.
# Reads NadirClawSentinel state file (which is updated every 60s) and
# probes the public Cloudflare endpoints. Offers to revive anything DOWN.
#
# Pin this to taskbar / desktop. Run anytime "is it working?" comes up.

$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "Dashboard Health"

function Color($txt, $fg) { Write-Host $txt -ForegroundColor $fg -NoNewline }
function Line($txt, $fg)  { Write-Host $txt -ForegroundColor $fg }

Write-Host ""
Line "=== Bryan's Stack — Health Check ===" Cyan
Write-Host ""

# --- Sentinel state ---
$stateFile = "C:\Users\Agile\Respositories\NadirClaw\sentinel_state.json"
$state = $null
if (Test-Path $stateFile) {
    try {
        $state = Get-Content $stateFile -Raw | ConvertFrom-Json
        $age = (Get-Date) - [datetime]$state.assessed_at
        if ($age.TotalMinutes -gt 5) {
            Line ("Sentinel state is STALE ({0:N0}m old) — sentinel may be hung." -f $age.TotalMinutes) Yellow
        } else {
            Line ("Sentinel last assessed {0:N0}s ago (PID {1})" -f $age.TotalSeconds, $state.sentinel_pid) DarkGray
        }
    } catch {
        Line "Sentinel state file unreadable" Red
    }
} else {
    Line "Sentinel state file missing — sentinel never started?" Red
}

# --- Local services (from sentinel state) ---
Write-Host ""
Line "Local services" White
if ($state -and $state.services) {
    foreach ($name in $state.services.PSObject.Properties.Name) {
        $svc = $state.services.$name
        $mark = if ($svc.healthy) { "[OK]" } else { "[DOWN]" }
        $color = if ($svc.healthy) { "Green" } else { "Red" }
        $reason = if ($svc.healthy) { "" } else { " — $($svc.reason)" }
        Color ("  {0,-7}" -f $mark) $color
        Color (" {0,-15}" -f $name) White
        Line $reason DarkGray
    }
} else {
    Line "  (no sentinel data)" DarkGray
}

# --- Public endpoints (through Cloudflare tunnel) ---
Write-Host ""
Line "Public endpoints (via Cloudflare)" White
$endpoints = @(
    @{ Name = "akdashboard"; Url = "https://akdashboard.agent-buddy.ai/" },
    @{ Name = "pipeline";    Url = "https://pipeline.agent-buddy.ai/" }
)
foreach ($e in $endpoints) {
    try {
        $r = Invoke-WebRequest -Uri $e.Url -Method Head -MaximumRedirection 5 -TimeoutSec 8 -UseBasicParsing -SkipHttpErrorCheck
        $code = $r.StatusCode
        $color = if ($code -lt 400) { "Green" } else { "Red" }
        $mark = if ($code -lt 400) { "[OK]" } else { "[$code]" }
        Color ("  {0,-7}" -f $mark) $color
        Color (" {0,-15}" -f $e.Name) White
        Line ("HTTP {0} — {1}" -f $code, $e.Url) DarkGray
    } catch {
        Color "  [DOWN] " Red
        Color (" {0,-15}" -f $e.Name) White
        Line ($_.Exception.Message.Substring(0, [Math]::Min(80, $_.Exception.Message.Length))) DarkGray
    }
}

Write-Host ""
$allOk = $true
if ($state -and $state.services) {
    foreach ($n in $state.services.PSObject.Properties.Name) {
        if (-not $state.services.$n.healthy) { $allOk = $false; break }
    }
}

if ($allOk) {
    Line "All green. Nothing to do." Green
    Write-Host ""
    Read-Host "Press Enter to close"
    exit 0
}

# --- Recovery prompt ---
Line "Some services are DOWN. Sentinel will heal automatically within 60-90s." Yellow
Line "Want to force a heal NOW? (requires admin)" Yellow
$ans = Read-Host "[Y]es / [N]o (default: N)"
if ($ans -notmatch "^[Yy]") {
    exit 0
}

Write-Host ""
Line "Triggering Sentinel restart (this re-runs all health checks)..." Cyan

# Restart NadirClawSentinel via elevated PowerShell — its main loop
# will detect failures and call the appropriate restart_* function.
Start-Process powershell -Verb RunAs -Wait -ArgumentList @(
    "-NoProfile",
    "-Command",
    "Restart-Service NadirClawSentinel -Force; Start-Sleep -Seconds 90; exit"
)

Line "Done. Re-run this script to verify." Green
Write-Host ""
Read-Host "Press Enter to close"
