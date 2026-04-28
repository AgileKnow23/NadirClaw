# Elevated one-shot recovery: install NadirClaw-Router NSSM service,
# start AkDashboard, and restart the Sentinel.
# Must run as Administrator. Triggered from Claude Code via -Verb RunAs.

$ErrorActionPreference = 'Stop'
$LogFile = 'C:\Users\Agile\Respositories\NadirClaw\fix_services_admin.log'

function Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

Log '=== fix_services_admin starting ==='

$Nssm        = 'C:\Users\Agile\AppData\Local\Microsoft\WinGet\Links\nssm.exe'
$NadirClawEx = 'C:\Users\Agile\AppData\Local\Programs\Python\Python312\Scripts\nadirclaw.exe'
$NadirClawDir = 'C:\Users\Agile\Respositories\NadirClaw'
$LogDir      = 'C:\Users\Agile\.nadirclaw\logs'

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

# 1. Free port 8856 so the new service can bind it
Log 'Stopping any python processes holding :8856'
$procs = Get-NetTCPConnection -LocalPort 8856 -ErrorAction SilentlyContinue |
    Where-Object { $_.State -eq 'Listen' } |
    Select-Object -ExpandProperty OwningProcess -Unique
foreach ($targetPid in $procs) {
    try {
        Log "  killing pid $targetPid"
        Stop-Process -Id $targetPid -Force -ErrorAction Stop
    } catch {
        Log "  failed to kill pid $targetPid : $_"
    }
}
Start-Sleep -Seconds 2

# 2. Install or reinstall NadirClaw-Router NSSM service
$svc = Get-Service -Name 'NadirClaw-Router' -ErrorAction SilentlyContinue
if ($svc) {
    Log 'NadirClaw-Router already exists — removing first'
    & $Nssm stop   NadirClaw-Router 2>&1 | Out-Null
    & $Nssm remove NadirClaw-Router confirm 2>&1 | Out-Null
    Start-Sleep -Seconds 2
}

Log 'Installing NadirClaw-Router via NSSM'
& $Nssm install NadirClaw-Router $NadirClawEx 'serve'
& $Nssm set NadirClaw-Router Description 'NadirClaw LLM Router (port 8856)'
& $Nssm set NadirClaw-Router AppDirectory $NadirClawDir
& $Nssm set NadirClaw-Router AppStdout (Join-Path $LogDir 'NadirClaw-Router.log')
& $Nssm set NadirClaw-Router AppStderr (Join-Path $LogDir 'NadirClaw-Router-error.log')
& $Nssm set NadirClaw-Router AppRotateFiles 1
& $Nssm set NadirClaw-Router AppRotateBytes 10485760
& $Nssm set NadirClaw-Router AppRestartDelay 5000
& $Nssm set NadirClaw-Router Start SERVICE_AUTO_START
# Run as the interactive user so it can read ~/.nadirclaw/.env credentials.
# Bryan = local 'Agile' account. NSSM accepts plain '.\username' with empty password
# only if the account has 'Log on as a service' rights; safer to inherit LocalSystem
# but make HOME explicit (NadirClaw resolves USER_HOME via env).
& $Nssm set NadirClaw-Router AppEnvironmentExtra `
    'PYTHONUNBUFFERED=1' `
    'NADIRCLAW_USER_HOME=C:\Users\Agile' `
    'USERPROFILE=C:\Users\Agile' `
    'HOME=C:\Users\Agile'

Log 'Starting NadirClaw-Router'
& $Nssm start NadirClaw-Router 2>&1 | ForEach-Object { Log "  nssm: $_" }
Start-Sleep -Seconds 12

$health = $null
try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8856/' -TimeoutSec 5
    Log "NadirClaw health: $($health | ConvertTo-Json -Compress)"
} catch {
    Log "NadirClaw health probe failed: $_"
}

# 3. Start AkDashboard
Log 'Starting AkDashboard service'
try {
    Start-Service AkDashboard -ErrorAction Stop
    Start-Sleep -Seconds 6
    Log "AkDashboard status: $((Get-Service AkDashboard).Status)"
} catch {
    Log "AkDashboard start failed: $_"
}

# 4. Restart Sentinel so it picks up the patched restart_nadirclaw and resets rate-limits
Log 'Restarting NadirClawSentinel'
Restart-Service NadirClawSentinel -Force
Start-Sleep -Seconds 3
Log "NadirClawSentinel status: $((Get-Service NadirClawSentinel).Status)"

Log '=== fix_services_admin done ==='
