# 빙구팩 자동 pull 스케줄러 등록 — owner 직접 실행용 (! pwsh -File <이 파일>)
# ChatGPT/claude 채팅 저장분(클라우드 inbox)을 5분마다 로컬 장부로 자동 pull.
# hosted 저장 시 이미 사람이 confirm('SAVE n')했으므로 그걸 사람-증거로 신뢰해 자동 commit.
$py = "C:\Users\PC\AppData\Local\Programs\Python\Python314\python.exe"
$script = "C:\Users\PC\binggupack\scripts\auto_pull_hosted.py"
$a = New-ScheduledTaskAction -Execute $py -Argument $script -WorkingDirectory "C:\Users\PC\binggupack"
$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "BingguPack_AutoPull" -Action $a -Trigger $t -Force -Description "BingguPack ChatGPT/claude 채팅 저장분 자동 로컬 pull (5분 주기)"
Write-Host "=== 자동 pull 스케줄러 등록 완료 (BingguPack_AutoPull, 5분 주기) ==="
Write-Host "해제하려면: Unregister-ScheduledTask -TaskName BingguPack_AutoPull -Confirm:`$false"
