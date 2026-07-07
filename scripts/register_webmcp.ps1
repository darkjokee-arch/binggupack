# 빙구팩 웹 MCP 로그온 자동가동 스케줄러 등록 — 사용자 셸에서 직접 실행.
# (공개 터널 노출은 사람 결정 — AI/자동화가 이 등록을 대행하지 않는 것이 정책)
# 실행: powershell -ExecutionPolicy Bypass -File <이 파일>
# 경로 자동탐지: repo=$PSScriptRoot 부모, python=pythonw 우선(무인 실행 창 0).
param(
  [string]$Python = "",
  [string]$TaskName = ""
)
# 작업명 = 기본 + (env BINGGU_TASK_SUFFIX). 같은 PC 다중 사용자 충돌 회피(미설정=회귀 0).
if (-not $TaskName) {
  $sfx = ($env:BINGGU_TASK_SUFFIX -replace '[^A-Za-z0-9_]', '')
  $TaskName = if ($sfx) { "BingguPack_WebMCP_$sfx" } else { "BingguPack_WebMCP" }
}
if (-not $Python) {
  $cand = @()
  $c = Get-Command pythonw -ErrorAction SilentlyContinue
  if ($c) { $cand += $c.Source }
  $c2 = Get-Command python -ErrorAction SilentlyContinue
  if ($c2) { $cand += ($c2.Source -replace 'python\.exe$', 'pythonw.exe'); $cand += $c2.Source }
  $Python = ($cand | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1)
}
if (-not $Python) { Write-Error "python(w)을 찾지 못했습니다 — -Python <경로> 로 지정하세요"; exit 1 }
$script = Join-Path $PSScriptRoot "start_binggu_web.py"
if (-not (Test-Path $script)) { Write-Error "start_binggu_web.py 없음: $script"; exit 1 }
Register-ScheduledTask -TaskName $TaskName `
  -Action (New-ScheduledTaskAction -Execute $Python -Argument "`"$script`"") `
  -Trigger (New-ScheduledTaskTrigger -AtLogOn) `
  -Principal (New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive) `
  -Description "빙구팩 로컬 MCP를 웹/앱 커넥터에 노출(HTTP+cloudflared quick tunnel). 로그온시 자동 가동." `
  -Force | Out-Null
Write-Host "=== $TaskName 등록 완료 — 재부팅/로그온 시 웹 MCP 자동 가동 ($Python) ==="
Write-Host "주소 확인: <home>/mcp_web_url.txt (quick tunnel — 재부팅 시 갱신)"
Write-Host "해제하려면: Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
