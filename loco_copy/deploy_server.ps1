# botup.md V12/V14 -- deploy the bot on the server in one command. Run in PowerShell ON THE SERVER.
#
#   From a server copy that already has this file:
#       powershell -ExecutionPolicy Bypass -File .\loco_copy\deploy_server.ps1
#   From an older server copy (this file not there yet) -- download it and point it at the project folder:
#       iwr https://raw.githubusercontent.com/sami7q/boot/main/loco_copy/deploy_server.ps1 -OutFile $env:TEMP\deploy.ps1
#       powershell -ExecutionPolicy Bypass -File $env:TEMP\deploy.ps1 -ProjectDir C:\path\to\project
#
# Steps: back up the database -> update the code (git pull, or the GitHub archive when the folder is not a git
# checkout; .env, data\ and .venv\ are never touched) -> install -> ruff + mypy + tests -> restart the bot
# (LocoCopy) and the dashboard API (LocoCopyApi) -> import the research results -> check every dashboard route.
# Nothing is restarted if a check fails. The Cloudflare tunnel is never touched (its URL must not change).
#   -ProjectDir X        the folder holding loco_copy\ (default: the parent of this script's folder)
#   -SkipTests           restart without running the checks (not recommended)
#   -ResearchJson X      import this research file instead of research\research_latest.json
#   -BotService / -ApiService   Windows service or scheduled task names (default LocoCopy / LocoCopyApi)
#   -IfChanged           do nothing when GitHub has no new commit (for the automatic updater)
#   -InstallAutoUpdate   register a scheduled task that runs this script with -IfChanged every 10 minutes
param(
    [string]$ProjectDir = "",
    [switch]$SkipTests,
    [string]$ResearchJson = "",
    [string]$BotService = "LocoCopy",
    [string]$ApiService = "LocoCopyApi",
    [switch]$IfChanged,
    [switch]$InstallAutoUpdate,
    [string]$Repo = "sami7q/boot",
    [string]$Branch = "main"
)
$ErrorActionPreference = "Stop"
if (-not $ProjectDir) { $ProjectDir = Split-Path $PSScriptRoot -Parent }
$ProjectDir = (Resolve-Path $ProjectDir).Path
$loco = Join-Path $ProjectDir "loco_copy"
if (-not (Test-Path (Join-Path $loco "pyproject.toml"))) { throw "$ProjectDir has no loco_copy\pyproject.toml -- pass -ProjectDir" }
$py = Join-Path $loco ".venv\Scripts\python.exe"
$cli = Join-Path $loco ".venv\Scripts\loco-copy.exe"
$isGit = (Test-Path (Join-Path $ProjectDir ".git")) -and [bool](Get-Command git -ErrorAction SilentlyContinue)  # else: archive
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"

function Step([string]$m) { Write-Host "`n== $m" -ForegroundColor Cyan }

function Get-RemoteHead {
    # the newest commit on GitHub (public API, no git needed on the server)
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $r = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/commits/$Branch" -TimeoutSec 20 `
            -Headers @{ "User-Agent" = "loco-copy-deploy" }
        return [string]$r.sha
    } catch { return "" }
}
Set-Location $loco

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

if ($InstallAutoUpdate) {
    $script = Join-Path $loco "deploy_server.ps1"
    $action = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -IfChanged -ProjectDir `"$ProjectDir`" -BotService $BotService -ApiService $ApiService"
    $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 10)
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
    Register-ScheduledTask -TaskName "LocoCopyAutoDeploy" -Action $action -Trigger $trigger -Settings $settings `
        -RunLevel Highest -User "SYSTEM" -Force | Out-Null
    Write-Host "Scheduled task LocoCopyAutoDeploy: every 10 min, deploys only when GitHub has a new commit and the checks pass." -ForegroundColor Green
    exit 0
}

if ($IfChanged) {
    $logDir = Join-Path $loco "data\logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    Start-Transcript -Path (Join-Path $logDir "autodeploy.log") -Append | Out-Null
    $remote = Get-RemoteHead
    $marker = Join-Path $loco "data\deployed_commit.txt"
    $local = if ($isGit) { (git -C $ProjectDir rev-parse HEAD).Trim() } elseif (Test-Path $marker) { (Get-Content $marker -Raw).Trim() } else { "" }
    if (-not $remote -or $remote -eq $local) { Write-Host "no new commit ($local)"; Stop-Transcript | Out-Null; exit 0 }
    Write-Host "new commit $remote (running $local)"
}

Step "1/7 back up the database"
& $cli backup
if ($LASTEXITCODE) { throw "backup failed -- nothing changed" }

Step "2/7 update the code"
function Update-FromArchive {
    # the GitHub archive, copied over the code only (never .env, data\, .venv\); the old code is backed up first
    $tmp = Join-Path $env:TEMP "loco-deploy-$stamp"
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $zip = Join-Path $tmp "src.zip"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri "https://github.com/$Repo/archive/refs/heads/$Branch.zip" -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $src = Get-ChildItem $tmp -Directory | Where-Object { Test-Path (Join-Path $_.FullName "loco_copy\pyproject.toml") } | Select-Object -First 1
    if (-not $src) { throw "the archive has no loco_copy\ -- nothing changed" }
    $backup = Join-Path $ProjectDir "_deploy\code-backup-$stamp"
    New-Item -ItemType Directory -Force -Path $backup | Out-Null
    robocopy $loco (Join-Path $backup "loco_copy") /E /XD .venv data __pycache__ .mypy_cache .pytest_cache .ruff_cache /NFL /NDL /NJH /NJS /NP | Out-Null
    robocopy (Join-Path $src.FullName "loco_copy") $loco /E /XD .venv data __pycache__ /XF .env /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "copying the new code failed (robocopy $LASTEXITCODE) -- old code is in $backup" }
    robocopy (Join-Path $src.FullName "research") (Join-Path $ProjectDir "research") /E /NFL /NDL /NJH /NJS /NP | Out-Null
    Write-Host "   code updated from the GitHub archive (old code kept in $backup)"
    return (Get-RemoteHead)
}
$commit = ""
if ($isGit) {
    git -C $ProjectDir pull --ff-only
    if ($LASTEXITCODE) {
        Write-Warning "git pull failed (files changed by hand on the server?) -- updating from the GitHub archive instead"
        $commit = Update-FromArchive
    } else {
        $commit = (git -C $ProjectDir rev-parse HEAD).Trim()
    }
} else {
    $commit = Update-FromArchive
}
Set-Location $loco

Step "3/7 install (refreshes the editable install; no new packages since V8)"
& $py -m pip install --quiet --disable-pip-version-check -e .
if ($LASTEXITCODE) { throw "pip install failed -- nothing restarted" }

Step "4/7 checks"
if ($SkipTests) {
    Write-Warning "checks skipped"
} else {
    & (Join-Path $loco "check.ps1")
    if ($LASTEXITCODE) { throw "checks failed -- nothing restarted, the running bot is untouched" }
}

Step "5/7 restart the bot and the dashboard API"
Restart-Unit $BotService
Restart-Unit $ApiService

Step "6/7 research results"
$candidates = @($ResearchJson, (Join-Path $ProjectDir "research\research_latest.json")) | Where-Object { $_ -and (Test-Path $_) }
if ($candidates) {
    & $cli research-import $candidates[0]
    if ($LASTEXITCODE) { throw "research import failed" }
} else {
    Write-Warning "no research_latest.json found -- the research pages will say so until one is imported"
}

Step "7/7 verify (every route must exist: 401/405, never 404)"
$port = & $py -c "from loco_copy.config import get_settings; print(get_settings().api_port)"
$base = "http://127.0.0.1:$port"
Start-Sleep -Seconds 8
$bad = @()
foreach ($path in "/api/health", "/api/research", "/api/portfolio/compare", "/api/portfolio/advice?wallet=x&coin=x", "/api/live") {
    try {
        $r = Invoke-WebRequest -Uri ($base + $path) -UseBasicParsing -TimeoutSec 5 -Method Head
        $code = [int]$r.StatusCode
    } catch {
        $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
    }
    $ok = ($code -eq 200 -or $code -eq 401 -or $code -eq 405)
    Write-Host ("   {0,-40} {1}  {2}" -f $path, $code, $(if ($ok) { "OK" } else { "FAIL" }))
    if (-not $ok) { $bad += $path }
}
if ($bad.Count) { throw "routes missing: $($bad -join ', ') -- check the API log" }
if ($commit) { Set-Content -Path (Join-Path $loco "data\deployed_commit.txt") -Value $commit }
Write-Host "`nDeployed $commit. Open the dashboard: the top strip must show the green live dot (botup.md V11)." -ForegroundColor Green
if ($IfChanged) { Stop-Transcript | Out-Null }
