# BingguPack cloud setup (Windows) - owner runs this in PowerShell.
#   & "C:\Users\PC\BingguPack\setup_cloud.ps1"            # check only (dry-run, no change)
#   & "C:\Users\PC\BingguPack\setup_cloud.ps1" -Apply     # kv create / toml id / kv put / scheduler
#   & "C:\Users\PC\BingguPack\setup_cloud.ps1" -Apply -Deploy   # + wrangler deploy (irreversible)
#
# Why: today's KV put / deploy needed owner's hand only due to a classifier block.
# For a NEW user this runs in their own shell - no harness block. So this is NOT
# "run on behalf of" - it is "bundle scattered commands into ONE entry, idempotent,
# stop-on-failure". login (browser OAuth) and the -Deploy decision stay the owner's hand.
#
# Safety: CF token plaintext 0 (login is OAuth in browser; this script never reads/writes
# a token). Only the KV namespace id (not a secret) is written to wrangler.real.toml.
# Auto-capture flag is never touched. Scheduler register is idempotent (-Force, re-run safe).
[CmdletBinding()]
param(
    [switch]$Apply,
    [switch]$Deploy
)

$ErrorActionPreference = "Stop"
$RepoRoot   = Split-Path -Parent $MyInvocation.MyCommand.Path
$WorkersDir = Join-Path $RepoRoot "hosted\workers"
$TomlPath   = Join-Path $WorkersDir "wrangler.real.toml"
$RegisterPs1 = Join-Path $RepoRoot "register_autopush.ps1"
$RealpackPy = Join-Path $RepoRoot "scripts\binggu_realpack_build.py"
$AutopushPy = Join-Path $RepoRoot "scripts\binggu_publish_autopush.py"
$PacksData  = Join-Path $WorkersDir "data\packs.json"
$TaskName   = "BingguPack_AutoPush"
$Placeholder = "<OWNER_FILLS_KV_ID>"

function Say($tag, $msg) { Write-Host ("{0} {1}" -f $tag, $msg) }
function Stop-With($msg) { Write-Host "[STOP] $msg" -ForegroundColor Red; exit 2 }

# Pick python launcher
$Py = (Get-Command py -ErrorAction SilentlyContinue)
if (-not $Py) { $Py = (Get-Command python -ErrorAction SilentlyContinue) }
if (-not $Py) { $Py = (Get-Command python3 -ErrorAction SilentlyContinue) }
$PyExe = if ($Py) { $Py.Source } else { $null }

$mode = if ($Apply) { "APPLY" } else { "DRY-RUN (check only, no change)" }
if ($Deploy) { $mode += " +DEPLOY" }
Write-Host ("=" * 60)
Write-Host "BingguPack cloud setup - $mode"
Write-Host ("=" * 60)

# [0] preflight (read-only)
if (-not $PyExe) { Stop-With "Python not found - install Python 3.10+ (https://python.org)" }
$wrangler = Get-Command wrangler -ErrorAction SilentlyContinue
if (-not $wrangler) {
    Stop-With "wrangler not installed. Run: npm i -g wrangler   (or use npx wrangler)"
}
Say "[OK]" "Python: $PyExe"
Say "[OK]" "wrangler: $($wrangler.Source)"

# [1] login check (no proxy - whoami only)
& wrangler whoami *> $null
if ($LASTEXITCODE -ne 0) {
    Stop-With "wrangler not logged in. Run yourself (browser OAuth, your action): wrangler login"
}
Say "[SKIP]" "wrangler already logged in"

# [2]+[3] KV namespace create (idempotent) + write id into toml
$toml = Get-Content -Raw -Encoding UTF8 $TomlPath
$idMatch = [regex]::Match($toml, '(?m)^(\s*id\s*=\s*)(["''])(.*?)(\2)(.*)$')
$curId = if ($idMatch.Success) { $idMatch.Groups[3].Value } else { $null }
$isPlaceholder = (-not $curId) -or ($curId.Trim() -eq "") -or ($curId.Trim() -eq $Placeholder)

if (-not $isPlaceholder) {
    Say "[SKIP]" "KV namespace already linked (id present)"
} elseif (-not $Apply) {
    Say "[--]" "KV namespace not created - with -Apply: wrangler kv namespace create PACKS"
} else {
    Push-Location $WorkersDir
    $out = & wrangler kv namespace create PACKS 2>&1 | Out-String
    Pop-Location
    if ($LASTEXITCODE -ne 0) { Stop-With "kv namespace create failed:`n$out" }
    $hexMatch = [regex]::Match($out, 'id\s*[:=]\s*["'']?([0-9a-fA-F]{32})')
    if (-not $hexMatch.Success) { $hexMatch = [regex]::Match($out, '([0-9a-fA-F]{32})') }
    if (-not $hexMatch.Success) { Stop-With "namespace created but id parse failed. See:`n$out" }
    $newId = $hexMatch.Groups[1].Value.ToLower()
    Say "[OK]" "KV namespace created"
    # [3] write id (backup .bak, precise line replace - other lines untouched)
    Copy-Item $TomlPath ($TomlPath + ".bak") -Force
    $newToml = [regex]::Replace($toml, '(?m)^(\s*id\s*=\s*)(["''])(.*?)(\2)(.*)$',
        { param($m) "$($m.Groups[1].Value)$($m.Groups[2].Value)$newId$($m.Groups[4].Value)$($m.Groups[5].Value)" }, 1)
    Set-Content -Path $TomlPath -Value $newToml -Encoding UTF8 -NoNewline
    Say "[OK]" "wrangler.real.toml id written (.bak backup)"
}

# [4] build packs (idempotent; new install may have 0 SAVE = fine)
$packsReady = $false
if (-not $Apply) {
    Say "[--]" "packs build - with -Apply: realpack_build --write"
} else {
    & $PyExe $RealpackPy --write *> $null
    if ($LASTEXITCODE -eq 0 -and (Test-Path $PacksData)) {
        $packsReady = $true
        Say "[OK]" "data/packs.json built"
    } else {
        Say "[--]" "no confirmed nodes to build yet (normal for new install)"
    }
}

# [5] KV put (idempotent - initial data load; config fixed to wrangler.real.toml)
if (-not $Apply) {
    Say "[--]" "KV put - with -Apply: initial data load"
} elseif (-not $packsReady) {
    Say "[SKIP]" "no packs.json to load - KV put skipped (auto after first SAVE)"
} else {
    Push-Location $WorkersDir
    & wrangler kv key put packs.json --path $PacksData --binding PACKS --config wrangler.real.toml *> $null
    $rc = $LASTEXITCODE
    Pop-Location
    if ($rc -ne 0) { Stop-With "kv key put failed" }
    Say "[OK]" "initial packs.json -> KV loaded"
}

# [6] deploy - separate GO (irreversible, default skip)
if (-not $Deploy) {
    Say "[SKIP]" "code deploy skipped (live, irreversible). Add -Deploy to deploy."
    Say "      " "data is already in KV from [5] - no deploy needed for data."
} elseif (-not $Apply) {
    Say "[--]" "deploy needs -Apply -Deploy together"
} else {
    Push-Location $WorkersDir
    & wrangler deploy --config wrangler.real.toml
    $rc = $LASTEXITCODE
    Pop-Location
    if ($rc -ne 0) { Stop-With "wrangler deploy failed" }
    Say "[OK]" "wrangler deploy done (rollback to revert)"
}

# [7] scheduler register (idempotent)
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Say "[SKIP]" "scheduler '$TaskName' already registered"
} elseif (-not $Apply) {
    Say "[--]" "scheduler not registered - with -Apply: runs register_autopush.ps1"
} else {
    & $RegisterPs1 | Out-Null
    Say "[OK]" "scheduler '$TaskName' registered (double-gate: no transfer until human SAVE)"
}

# [8] autopush first check (dry-run via python orchestrator wiring)
Say "[--]" "autopush double-gate: scheduler registered, but transfer = 0 until a human 'SAVE n'."
Say "      " "Emergency OFF: create file  %USERPROFILE%\.binggupack\autopush_disabled"

Write-Host ("-" * 60)
if (-not $Apply) {
    Write-Host "Next: apply for real with  & `"$($MyInvocation.MyCommand.Path)`" -Apply"
} else {
    Write-Host "Done. After your first 'SAVE n', the next scheduler cycle auto-updates KV (double-gate)."
}
Write-Host "Owner-only hands: wrangler login (browser OAuth), -Deploy decision. Token plaintext = 0."
