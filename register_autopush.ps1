# BingguPack autopush 스케줄러 등록 (owner 1회 실행 전용)
# 실행: PowerShell 에서  & "C:\Users\PC\binggupack\register_autopush.ps1"
# autopush 자체가 이중게이트(사람 SAVE 기록 없으면 전송 0) — 이 스크립트는 "주기 실행"만 등록.
$act = New-ScheduledTaskAction -Execute "py" -Argument "C:\Users\PC\binggupack\scripts\binggu_publish_autopush.py"
$trg = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(2)) -RepetitionInterval (New-TimeSpan -Minutes 10) -RepetitionDuration ([TimeSpan]::FromDays(3650))
$set = New-ScheduledTaskSettingsSet -StartWhenAvailable -Hidden -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "BingguPack_AutoPush" -Action $act -Trigger $trg -Settings $set -Description "BingguPack SAVE->KV auto upload (double-gate)" -Force | Out-Null
Write-Host "=== 등록 결과 ==="
Get-ScheduledTask -TaskName "BingguPack_AutoPush" | Select-Object TaskName, State | Format-List
