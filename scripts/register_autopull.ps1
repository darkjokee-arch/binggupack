# 빙구팩 자동 pull 스케줄러 등록 — 사용자 셸에서 직접 실행 (! pwsh -File <이 파일>)
# ChatGPT/claude 채팅 저장분(클라우드 inbox)을 5분마다 로컬 장부로 자동 pull.
# hosted 저장 시 이미 사람이 confirm('SAVE n')했으므로 그걸 사람-증거로 신뢰해 자동 commit.
# 경로 자동탐지(사용자 무관): repo=$PSScriptRoot 부모, python=pythonw 우선(무인 실행 창 0).
param(
  [string]$Python = "",
  [string]$Repo = ""
)
if (-not $Repo) { $Repo = Split-Path -Parent $PSScriptRoot }
if (-not $Python) {
  $cand = @()
  $c = Get-Command pythonw -ErrorAction SilentlyContinue
  if ($c) { $cand += $c.Source }
  $c2 = Get-Command python -ErrorAction SilentlyContinue
  if ($c2) { $cand += ($c2.Source -replace 'python\.exe$', 'pythonw.exe'); $cand += $c2.Source }
  $Python = ($cand | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1)
}
if (-not $Python) { Write-Error "python(w)을 찾지 못했습니다 — -Python <경로> 로 지정하세요"; exit 1 }
$script = Join-Path $PSScriptRoot "auto_pull_hosted.py"
if (-not (Test-Path $script)) { Write-Error "auto_pull_hosted.py 없음: $script"; exit 1 }
$a = New-ScheduledTaskAction -Execute $Python -Argument "`"$script`"" -WorkingDirectory $Repo
$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "BingguPack_AutoPull" -Action $a -Trigger $t -Force -Description "BingguPack ChatGPT/claude 채팅 저장분 자동 로컬 pull (5분 주기)"
Write-Host "=== 자동 pull 스케줄러 등록 완료 (BingguPack_AutoPull, 5분 주기, $Python) ==="
Write-Host "해제하려면: Unregister-ScheduledTask -TaskName BingguPack_AutoPull -Confirm:`$false"
