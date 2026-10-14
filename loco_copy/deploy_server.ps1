# botup.md V12 -- deploy the bot on the server in one command (run in PowerShell on the server):
#     powershell -ExecutionPolicy Bypass -File .\loco_copy\deploy_server.ps1
# Steps: back up the database -> git pull -> install -> ruff + mypy + tests -> restart the bot (LocoCopy) and the
# dashboard API (LocoCopyApi) -> import the research results -> check that every dashboard route answers.
# Nothing is restarted if a check fails. The Cloudflare tunnel is never touched (its URL must not change).
#   -SkipTests        restart without running the checks (not recommended)
#   -ResearchJson X   import this research file instead of research\research_latest.json from the repo
#   -BotService / -ApiService   service (or scheduled task) names, default LocoCopy / LocoCopyApi
param(
    [switch]$SkipTests,
    [string]$ResearchJson = "",
    [string]$BotService = "LocoCopy",
    [string]$ApiService = "LocoCopyApi"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$repo = Split-Path $PSScriptRoot -Parent
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$cli = Join-Path $PSScriptRoot ".venv\Scripts\loco-copy.exe"

function Step([string]$m) { Write-Host "`n== $m" -ForegroundColor Cyan }

function Restart-Unit([string]$name) {
    $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($svc) {
        Restart-Service -Name $name -Force
        (Get-Service -Name $name).WaitForStatus("Running", [TimeSpan]::FromSeconds(60))
        Write-Host "   service $name restarted"
        return
    }
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($task) {
        Stop-ScheduledTask -TaskName $name
        Start-Sleep -Seconds 3
        Start-ScheduledTask -TaskName $name
        Write-Host "   scheduled task $name restarted"
        return
    }
    throw "No Windows service or scheduled task named '$name' -- pass -BotService / -ApiService with the right names."
}

Step "1/7 back up the database"
& $cli backup
if ($LASTEXITCODE) { throw "backup failed -- nothing changed" }

Step "2/7 pull the latest code"
if (Test-Path (Join-Path $repo ".git")) {
    git -C $repo pull --ff-only
    if ($LASTEXITCODE) { throw "git pull failed (local edits on the server?) -- nothing restarted" }
} else {
    Write-Warning "$repo is not a git checkout: copy the files of botup.md V9-V12 into it first, then run this again."
}

Step "3/7 install (no new packages in V9-V12; this only refreshes the editable install)"
& $py -m pip install --quiet --disable-pip-version-check -e .
if ($LASTEXITCODE) { throw "pip install failed -- nothing restarted" }

Step "4/7 checks"
if ($SkipTests) {
    Write-Warning "checks skipped"
} else {
    & (Join-Path $PSScriptRoot "check.ps1")
    if ($LASTEXITCODE) { throw "checks failed -- nothing restarted, the running bot is untouched" }
}

Step "5/7 restart the bot and the dashboard API"
Restart-Unit $BotService
Restart-Unit $ApiService

Step "6/7 research results"
$candidates = @($ResearchJson, (Join-Path $repo "research\research_latest.json")) | Where-Object { $_ -and (Test-Path $_) }
if ($candidates) {
    & $cli research-import $candidates[0]
    if ($LASTEXITCODE) { throw "research import failed" }
} else {
    Write-Warning "no research_latest.json found -- the research pages will say so until one is imported"
}

Step "7/7 verify (every route must exist: 200 or 401, never 404)"
$port = & $py -c "from loco_copy.config import get_settings; print(get_settings().api_port)"
$base = "http://127.0.0.1:$port"
Start-Sleep -Seconds 8
$bad = @()
foreach ($path in "/api/health", "/api/research", "/api/portfolio/compare", "/api/portfolio?group=mix2ac4", "/api/live") {
    try {
        $r = Invoke-WebRequest -Uri ($base + $path) -UseBasicParsing -TimeoutSec 5 -Method Head
        $code = [int]$r.StatusCode
    } catch {
        $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
    }
    $ok = ($code -eq 200 -or $code -eq 401 -or $code -eq 405)
    Write-Host ("   {0,-32} {1}  {2}" -f $path, $code, $(if ($ok) { "OK" } else { "FAIL" }))
    if (-not $ok) { $bad += $path }
}
if ($bad.Count) { throw "routes missing: $($bad -join ', ') -- check the API log" }
Write-Host "`nDeployed. Open the dashboard: the top strip must show the green live dot (botup.md V11)." -ForegroundColor Green
