# BingguPack harvest 스케줄러 등록 (owner 1회 실행 전용)
# 실행: PowerShell 에서  & "C:\Users\PC\binggupack\register_harvest.ps1"
# harvest 자체가 3중게이트(등록 소스만·후보로만·영구는 사람 SAVE) — 이 스크립트는 "주기 실행"만 등록.
# autopush(출력)와 별개 task(BingguPack_Harvest, inbound) — 기존 autopush 미접촉.
#
# 실 네트워크 fetch 는 이 owner 스케줄러 프로세스에서만 일어난다(Claude tool_use 아님).
# 등록된 소스가 0개면 수확도 0(fail-closed) — 먼저  binggu.py harvest add  로 소스를 등록할 것.

# python launcher 절대경로 resolve. 스케줄러는 비대화형이라 PATH 가 보장되지 않음 →
# "py"(이름만)로 등록하면 0x80070002(파일 못 찾음)로 매 사이클 실패. 절대경로 + WorkingDirectory 필수.
$Py = (Get-Command py -ErrorAction SilentlyContinue)
if (-not $Py) { $Py = (Get-Command python -ErrorAction SilentlyContinue) }
if (-not $Py) { $Py = (Get-Command python3 -ErrorAction SilentlyContinue) }
if (-not $Py) { Write-Host "[STOP] python launcher (py/python/python3) not found in PATH" -ForegroundColor Red; exit 2 }
$PyExe      = $Py.Source
$RepoRoot   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptPath = Join-Path $RepoRoot "scripts\binggu_harvest.py"

# 사전점검: 대상 스크립트가 실제로 있어야 등록(없는 경로로 등록하면 매 사이클 0x80070002).
if (-not (Test-Path $ScriptPath)) {
    Write-Host "[STOP] harvest script not found: $ScriptPath" -ForegroundColor Red; exit 3
}
Write-Host "python: $PyExe"
Write-Host "script: $ScriptPath"

$act = New-ScheduledTaskAction -Execute $PyExe -Argument ('"{0}"' -f $ScriptPath) -WorkingDirectory $RepoRoot
# inbound 수확은 외부 fetch 라 주기를 길게(1시간). autopush(10분)보다 느슨.
$trg = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(3)) -RepetitionInterval (New-TimeSpan -Hours 1) -RepetitionDuration ([TimeSpan]::FromDays(3650))
# ExecutionTimeLimit 15분: 외부 fetch 는 네트워크 지연이 커 여유 필요. 짧으면 강제 종료(0xC000013A)
#   + 죽은 인스턴스 누적(0x800710E0)으로 이후 실행 거부.
# 배터리 조건 해제: 기본값(DisallowStartIfOnBatteries)이면 노트북 배터리 사용 시 거부(0x800710E0).
# MultipleInstances IgnoreNew: harvest 는 ledger candidate write 를 하므로 동시 실행 금지(더 중요).
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -Hidden `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "BingguPack_Harvest" -Action $act -Trigger $trg -Settings $set -Description "BingguPack external harvest (registered sources -> candidate only, triple-gate)" -Force | Out-Null
Write-Host "=== 등록 결과 ==="
Get-ScheduledTask -TaskName "BingguPack_Harvest" | Select-Object TaskName, State | Format-List
Write-Host "긴급 정지:  New-Item `"$env:USERPROFILE\.binggupack\harvest_disabled`" -ItemType File"
