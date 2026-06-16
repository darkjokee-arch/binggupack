# BingguPack autopush 스케줄러 등록 (owner 1회 실행 전용)
# 실행: PowerShell 에서  & "C:\Users\PC\binggupack\register_autopush.ps1"
# autopush 자체가 이중게이트(사람 SAVE 기록 없으면 전송 0) — 이 스크립트는 "주기 실행"만 등록.

# python launcher 절대경로 resolve. 스케줄러는 비대화형이라 PATH 가 보장되지 않음 →
# "py"(이름만)로 등록하면 0x80070002(파일 못 찾음)로 매 사이클 실패. 절대경로 + WorkingDirectory 필수.
$Py = (Get-Command py -ErrorAction SilentlyContinue)
if (-not $Py) { $Py = (Get-Command python -ErrorAction SilentlyContinue) }
if (-not $Py) { $Py = (Get-Command python3 -ErrorAction SilentlyContinue) }
if (-not $Py) { Write-Host "[STOP] python launcher (py/python/python3) not found in PATH" -ForegroundColor Red; exit 2 }
$PyExe      = $Py.Source
$RepoRoot   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ScriptPath = Join-Path $RepoRoot "scripts\binggu_publish_autopush.py"

$act = New-ScheduledTaskAction -Execute $PyExe -Argument ('"{0}"' -f $ScriptPath) -WorkingDirectory $RepoRoot
$trg = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(2)) -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration ([TimeSpan]::FromDays(3650))
# ExecutionTimeLimit 15분: wrangler 로컬 설치(setup-cloud [0d]) 시 1초지만, 첫 실행/네트워크 지연
#   대비 여유. 5분이면 wrangler 재다운로드가 한도를 넘겨 작업이 강제 종료(0xC000013A)되고
#   죽은 인스턴스가 누적돼 이후 실행이 거부된다(0x800710E0).
# 배터리 조건 해제: 기본값(DisallowStartIfOnBatteries)이면 노트북 배터리 사용 시 작업이
#   거부(0x800710E0)/중단된다 → -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries.
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -Hidden `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "BingguPack_AutoPush" -Action $act -Trigger $trg -Settings $set -Description "BingguPack SAVE->KV auto upload (double-gate)" -Force | Out-Null
Write-Host "=== 등록 결과 ==="
Get-ScheduledTask -TaskName "BingguPack_AutoPush" | Select-Object TaskName, State | Format-List
