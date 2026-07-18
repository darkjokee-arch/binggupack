# Install BingguPack

> 현재 버전은 [releases/latest](https://github.com/darkjokee-arch/binggupack/releases/latest)를 참고하세요 — **MCP 도구 · HTTP 모드(웹/앱 커넥터) · ChatGPT 저장 채널 · 원클릭 온보딩(`binggu onboard`) · backup/export/restore** ([CHANGELOG](CHANGELOG.md) 참조).
> PyPI 반영이 늦으면 아래 `git clone`으로 최신을 사용하세요.
> 설치 직후 60초 체험: `binggu demo`

> `scripts/`·`docs/`의 `openbinggu_`/`OPENBINGGU_` 접두사는 레거시 내부 코드네임입니다(BingguPack과 동일 프로젝트).

이 문서는 **실전 설치 절차** 중심입니다. 개념·설계는 [README.md](README.md), 따라하기는 [docs/BINGGUPACK_TUTORIAL.md](docs/BINGGUPACK_TUTORIAL.md)를 보세요.

설치 흐름: **PyPI 빠른 설치 또는 Clone → Verify → MCP sandbox 등록 → 재시작 → 도구 확인 → save gate 확인 → 운영 home 보호 → Troubleshooting**.

---

## Requirements

- **Python 3.10+** (표준 라이브러리 위주, 외부 런타임 의존성 0)
- **git** — 없으면 GitHub **Code → Download ZIP**으로 받아 압축 해제해도 됩니다.
- **Claude Code** — MCP sandbox 등록을 하려면 필요(`claude` CLI).
- OS: Windows / WSL / macOS / Linux — 같은 정책으로 동작.
- python 런처: Windows `py` · WSL/macOS/Linux `python3`. 아래 예시의 `python`을 OS에 맞게 바꿔 쓰세요.
- (선택) hosted/MCP·semantic 도장: Node.js + `wrangler`, Ollama `bge-m3`. 로컬 CLI만 쓰면 불필요.

## PyPI quick install

로컬 CLI만 쓰려면 이 경로가 가장 짧습니다.

> `pip install binggupack`는 PyPI 최신 배포판을 설치합니다. PyPI 반영이 늦거나 최신 기능이 필요하면 아래 [Clone / source install](#clone--source-install)을 사용하세요.

```bash
python -m pip install binggupack
binggu start
binggu remember "배포 전에 live endpoint를 먼저 확인한다"
binggu doctor
python -m binggupack doctor
```

- `binggu`와 `python -m binggupack`은 같은 CLI를 실행합니다.
- `remember`는 미리보기만 합니다. 실제 저장은 화면에 나온 `save ... --confirm "SAVE n"`을 사람이 실행할 때만 됩니다.
- pip 설치 후에도 stdio MCP를 등록할 수 있습니다(진입점 `openbinggu-mcp-server` · 아래 [pip 설치 사용자](#pip-설치-사용자--진입점으로-등록) 참조). `scripts/` selftest는 clone 경로의 개발/검증 절차입니다.

## Clone / source install

bash (macOS / WSL / Linux):
```bash
git clone https://github.com/darkjokee-arch/binggupack.git
cd binggupack
```

PowerShell (Windows):
```powershell
git clone https://github.com/darkjokee-arch/binggupack.git
cd binggupack
```

## Verify clean install

등록 전에 오프라인으로 검증합니다(write 0, 운영 `~/.binggupack` 미접촉):

```bash
python scripts/smoke_test.py --home ./_binggu_test_home
#  기대: 11/11 PASS · 9.save_no_confirm_REJECT_write0 PASS · 9b.save_exact_confirm_isolated_write PASS · 10.operating_ledger_write_0 PASS

python scripts/openbinggu_doctor.py --selftest                  # GATE=GO (운영 정합, write 0)
python scripts/openbinggu_doctor.py --tree examples/toy_project # CLEAN
```

> 더 깊은 모듈별 selftest(capture · publish · cross-platform · candidate UX 등) 전체 목록은 [docs/BINGGUPACK_TUTORIAL.md](docs/BINGGUPACK_TUTORIAL.md)와 각 스크립트 `--selftest`를 참고하세요.

## Install Claude Code MCP sandbox entry

**clone만으로 MCP 서버가 포함**됩니다(`scripts/openbinggu_mcp_server.py`). sandbox 엔트리로 등록(미리보기 → 실제):

```bash
python scripts/install_claude_mcp.py --sandbox --home ./_binggu_test_home --dry-run   # 명령 미리보기
python scripts/install_claude_mcp.py --sandbox --home ./_binggu_test_home --apply     # 실제 claude mcp add
```

- `BINGGU_HOME`으로 sandbox/운영 home 분리(미설정 시 `~/.binggupack`). installer가 MCP config `env`에 주입합니다.
- 운영 엔트리 `openbinggu-local`은 installer가 건드리지 않습니다(거부). sandbox 이름만 등록됩니다.

### pip 설치 사용자 — 진입점으로 등록

`pip install binggupack` 후에는 **canonical 진입점 `binggupack-mcp`** 로 clone 없이 등록할 수 있습니다. 기본은 **core profile**(작고 명확한 표면: 상태·회상·근거·목록·검토·미리보기 + 승인 기반 저장/페어/폐기/교체). `<작업폴더>`는 MCP가 파일 접근을 허용할 루트(경로 게이트 allow_root)입니다:

```bash
claude mcp add binggupack -s user \
  -e BINGGU_HOME="$HOME/.binggupack" \
  -- binggupack-mcp --serve "$HOME/binggu-workspace"
```

전체 도구(현재 advanced 표면)가 필요하면 `--profile advanced` 를 붙입니다:

```bash
  -- binggupack-mcp --serve "$HOME/binggu-workspace" --profile advanced
```

기존 진입점 `openbinggu-mcp-server` 도 그대로 동작하며 **기본이 전체(advanced) 도구**입니다(하위호환). Legacy-compatible entry point. No removal date is currently scheduled.

### Codex 등록 — `~/.codex/config.toml`

Codex(및 다른 Rust rmcp 클라이언트)는 설정 파일에 MCP 서버 블록을 추가합니다. 빙구팩 서버는 표준 stdio JSON-RPC이고 tools/list가 MCP 표준 필드(name/description/inputSchema)만 노출하므로 그대로 호환됩니다:

```toml
[mcp_servers.openbinggu-local]
# 권장(전 OS 안정) — python 을 절대경로로 직접 실행:
command = 'C:\path\to\python.exe'    # 예: ...\Programs\Python\Python3xx\python.exe
args = ['C:\path\to\repo\scripts\openbinggu_mcp_server.py', '--serve', 'C:\작업폴더']
[mcp_servers.openbinggu-local.env]
BINGGU_HOME = 'C:\Users\<you>\.binggupack'
```

> ⚠️ **Windows 주의**: `command = "openbinggu-mcp-server"`(pip 진입점 이름)로도 등록되지만, Windows에서 Codex(Rust)가 실행할 때 **pip 진입점 .exe launcher stub + PATH 문제로 `tools/list` 30초 timeout**이 날 수 있습니다(서버는 정상, 클라이언트 spawn 문제). **python 을 절대경로로 직접 실행**하면 stub·PATH 둘 다 배제돼 안정적입니다. TOML은 Windows 경로에 `'…'`(작은따옴표=literal)를 쓰세요.

등록 후 Codex를 재시작하면 로그에 `tool_count=30`·`has_cached_tools=true`가 찍힙니다(timeout이면 위 python 직접 방식으로 교체).

## Restart Claude Code

MCP 도구는 **세션 시작 시 고정**됩니다. 등록 후 **Claude Code를 반드시 재시작**해야 도구가 노출됩니다.

## Confirm MCP tools

재시작 후 sandbox 서버 연결과 30도구 노출을 확인합니다:

```bash
claude mcp list
claude mcp get openbinggu-local-sandbox    # Status: Connected · env BINGGU_HOME 확인
```

노출되어야 하는 **30 MCP 도구**:

- **조회(read · 20)**: `selftest` · `status` · `list` · `recall` · `preflight` · `trace_review` · `trace_show` · `reminders` · `reflect` · `capture_classify` · `capture_preview` · `pack_validate` · `consumer_smoke` · `harvest_list` · `cloud_recall` · `cloud_packs` · `cloud_search` · `why` · `contrast` · `abstraction`
- **dry-run(2)**: `pack_build` · `publish_guard_dryrun`
- **쓰기(write-gated · 8 · 도구별 confirm 문구 정확 일치 시에만 실행)**: `save_candidate` · `pair` · `deprecate` · `replace` · `harvest_add` · `harvest_remove` · `mark_hit` · `mark_miss`

`harvest_run`(실 네트워크 수확) 등 위험 도구는 MCP에 노출되지 않습니다(차단 목록).

## Confirm save gate

자동/무단 저장은 차단되어야 정상입니다. `save_candidate`는 `dry_run` 기본이고, `dry_run=false`로 호출해도:

- confirm 부재/불일치 → **REJECT** (`confirm_phrase_mismatch`) · `executed_write=false` · write 0
- `"SAVE n"` confirm 은 **형식 검증**일 뿐 사람 승인 증거가 아닙니다(모델이 dry-run 응답을 재현 가능). 실 저장은 **사람 앵커**(키보드 `SAVE n` → `save_gate`)가 있을 때만 human 승격입니다(2026-07-13 개정: 저장 경로 승격은 사람 `SAVE n` 앵커 단일 원칙 — trusted approval 은 비-저장 mutation 전용, SECURITY.md 참조) — 저장 위치는 `BINGGU_HOME` 장부(sandbox 등록이면 sandbox home, 운영 ledger 불변)

confirm 없이 차단되는 건 실패가 아니라 PASS입니다. 쓰기 도구 8종(`save_candidate`/`pair`/`deprecate`/`replace`/`harvest_add`/`harvest_remove`/`mark_hit`/`mark_miss`) 전부 같은 방식의 confirm 게이트(도구별 문구 정확 일치)를 씁니다.

## Operating home vs sandbox home

- **운영 home** (`~/.binggupack`) — 실제 장부 `ledger.sqlite`. installer/sandbox는 여기를 **건드리지 않습니다**.
- **sandbox/test home** (`--home` 으로 지정) — preview/cache 흔적만 남습니다.
- 분리 원칙: `BINGGU_HOME`을 sandbox 경로로 주입하므로, sandbox MCP 호출이 운영 ledger에 durable write를 남기지 않습니다(smoke_test의 `operating_ledger_write_0`이 강제).

## Web/app connector — HTTP 모드 (optional)

Claude 웹/앱 커넥터에서 로컬 30도구(advanced 프로파일)를 그대로 쓰려면, 로컬 MCP 서버를 HTTP 모드로 열고 터널 뒤에 둡니다:

```bash
BINGGU_MCP_PATH_TOKEN=<경로토큰> python scripts/openbinggu_mcp_server.py --http <PORT> <ROOT>
```

- `127.0.0.1`에만 바인딩됩니다. 외부 노출은 Cloudflare Tunnel 등 터널을 앞에 두는 구성을 전제합니다.
- 경로 토큰은 `BINGGU_MCP_PATH_TOKEN` env로만 주입합니다(코드/설정 평문 0).
- stdio 등록과 같은 도구·같은 게이트입니다(쓰기 8종은 여기서도 confirm 정확 일치 필수).

**터널 준비물 · 주소 안정성**

- `cloudflared` 설치 필요: [공식 가이드](https://developers.cloudflare.com/cloudflared/download-and-install/) 후 PATH 추가 (Windows `winget install Cloudflare.cloudflared` · macOS `brew install cloudflared`).
- **quick tunnel(trycloudflare)은 재시작·재부팅마다 주소가 바뀝니다.** 그때마다 `~/.binggupack/mcp_web_url.txt`의 새 주소를 커넥터에 다시 붙여야 합니다.
- **고정 주소가 필요하면 named tunnel** (본인 Cloudflare 계정 + 도메인 필요): `cloudflared tunnel login` → `cloudflared tunnel create binggu` → `cloudflared tunnel route dns binggu mcp.내도메인.com` → tunnel config `service: http://127.0.0.1:8790`. 주소가 고정돼 커넥터를 한 번만 등록하면 됩니다.
- 부팅 자동 가동: `binggu onboard --webmcp --apply`가 로그온 시 자동 기동(`BingguPack_WebMCP` 스케줄 작업)을 등록합니다(공개 터널=본인 결정 옵트인).

## ChatGPT 저장 채널 (optional · hosted)

ChatGPT 채팅에서 `SAVE n`으로 승인한 것만 hosted inbox에 잠깐 적재되고, 내 PC가 서명키로 pull해 로컬 staging에 회수합니다(로컬 장부 확정은 PC측 `SAVE n` · 무인 저장 0 · PII 백스톱 reject).

### 원클릭 온보딩 — `binggu onboard`

> ⚠️ 이 저장 채널은 `hosted/` worker 소스가 필요합니다. **sdist 배포판엔 `hosted/workers/src`가 포함**되지만 **wheel 배포판엔 미포함**입니다 — wheel로 설치했다면 `binggu onboard`가 첫 단계(s0)에서 멈추니, **sdist**(`pip download --no-binary :all: binggupack`)로 받거나 **`git clone` 후** 실행하세요. (관리형 SaaS/멀티테넌트 호스팅은 범위 밖 — 본인 Cloudflare 계정에 직접 배포하는 소스만 동봉합니다.) Cloudflare 계정이 없으면 먼저 [무료 가입](https://dash.cloudflare.com/sign-up).

본인 Cloudflare 계정에 읽기 worker + 저장 채널(save_mcp) + auto-pull 스케줄러를 한 번에 셋업합니다(멱등 · dry-run 기본):

```bash
python binggu.py onboard                  # 1) 점검만 — 무엇이 될지 확인(변경 0)
npx wrangler login                        # 2) 본인 CF 로그인(브라우저 OAuth — 대행 없음)
python binggu.py onboard --apply --deploy # 3) 키 자동생성 + worker 배포 + 스케줄러 등록
python binggu.py onboard --show-url       # 4) ChatGPT 커넥터에 붙여넣을 전체 URL 확인
```

- 경로키/서명키는 `secrets.token_hex`로 자동 생성돼 `<repo>/../workers_port/.dev.vars.save_mcp`(repo 밖)에 저장되고, worker에는 stdin으로만 주입됩니다(argv/히스토리/출력 노출 0 — 화면 표시는 항상 앞8자 마스킹).
- 커넥터 등록: ChatGPT 설정 → 커넥터 → MCP 서버 URL에 `--show-url`로 확인한 주소(`…/mcp2/<경로키>`)를 붙여넣기. 이 URL은 비밀입니다.

**커넥터 URL은 용도가 2종입니다 — 헷갈리지 마세요:**

| 커넥터 | 용도 | URL 형태 | 등록처 |
|---|---|---|---|
| **저장 채널(save_mcp)** | ChatGPT에서 `SAVE n` 승인 → inbox 적재 → PC pull | `…workers.dev/mcp2/<경로키>` | ChatGPT 커넥터 |
| **웹 MCP(30도구)** | Claude 웹/앱에서 로컬 30도구 직접 사용 | `…trycloudflare.com/mcp/<토큰>` (named tunnel이면 고정 주소) | Claude.ai / 앱 커넥터 |

- 저장 채널(위 온보딩)은 `hosted/` worker, 웹 MCP는 로컬 `--http` 서버 + 터널 — 서로 다른 채널입니다.
- staging 회수(무인 저장 아님): `scripts/auto_pull_hosted.py`(후보를 staging 까지만 자동 회수 · 로컬 장부 확정은 PC에서 사람 `SAVE n`) + `scripts/register_autopull.ps1`(Windows 스케줄러 등록 — 경로 자동탐지 · mac/linux는 cron 라인 안내).
- hosted 경로 설계·경계: [docs/BINGGUPACK_SAVE_INTENT_V2A_MCP_CONNECTOR_DESIGN.md](docs/BINGGUPACK_SAVE_INTENT_V2A_MCP_CONNECTOR_DESIGN.md) · [docs/BINGGUPACK_HOSTED_BOUNDARY.md](docs/BINGGUPACK_HOSTED_BOUNDARY.md).

## Troubleshooting

- **도구가 안 보임** — Claude Code를 재시작했는지 확인(도구는 세션 시작 시 고정). `claude mcp get openbinggu-local-sandbox`가 Connected인지 확인.
- **`claude` 명령을 못 찾음 (Windows)** — installer가 `claude.cmd` shim을 `shutil.which`로 처리합니다. PATH에 Claude Code가 있는지 확인.
- **운영 엔트리를 덮어쓰려 함** — installer는 `openbinggu-local`(운영)을 거부합니다. sandbox 이름(`--sandbox`)으로 등록하세요.
- **selftest 실패** — `python scripts/openbinggu_doctor.py --selftest`로 GATE 상태를 먼저 확인. write는 항상 0이므로 운영 데이터 손상 없이 재시도 가능.

## Interactive save gate (optional)

기존 explicit confirm 방식은 그대로 유지되고, TTY에서 후보 선택을 돕는 보조 UX가 추가됐습니다.

```bash
python -m binggupack.cli.interactive_save            # TTY 대화형 (후보 선택 → confirm phrase 구성)
python -m binggupack.cli.interactive_save --selftest # 비-TTY 검증 (저장 0)
```

- non-TTY(CI/pipe/AI)에서는 **fail-closed** — 기존 explicit confirm 방식이 강제됩니다.
- 마지막 human confirmation과 실제 저장 게이트(`G4_no_auto`)는 기존 경로 그대로. interactive는 ledger에 직접 쓰지 않습니다.

## Developer: package build (optional)

PyPI publish는 v1.17.0부터 Trusted Publisher(OIDC)로 자동화돼 있습니다(태그 릴리스 → GitHub Actions). 로컬에서 패키지 build만 확인하려면 격리 venv에서:

```bash
python -m venv .build_venv
.build_venv/Scripts/python -m pip install build   # WSL/macOS: .build_venv/bin/python
.build_venv/Scripts/python -m build                # sdist + wheel 생성 (dist/)
```

- `pyproject.toml`에 `[build-system]` + `binggupack` 패키지 정의가 있습니다.
- build 산출물(`dist/`, `.build_venv/`)은 `.gitignore` 대상입니다.
- 패키지 설치 CLI 회귀는 `python tests/package_cli_selftest.py`로 확인합니다.

**sdist 시크릿 게이트(빌드 직후 필수 · CI/로컬)** — hosted worker 소스(`MANIFEST.in`)는 **sdist에만** 동봉되고 시크릿(`.dev.vars*`)은 **절대 포함되면 안 됩니다**. 빌드 직후 확인:

```bash
python -m build
tar tzf dist/*.tar.gz | grep -c "dev.vars"           # → 0 이어야 함(시크릿 0)
tar tzf dist/*.tar.gz | grep -c "hosted/workers/src"  # → >0 (worker 소스 포함 확인)
```

## 화자 축 사용 (v1.12.0+ · 양방향 페어 v1.14.0)

설치 후 내 말(owner)과 AI 요약(ai)을 따로 쌓는 화자 축은 로컬 CLI로 씁니다. 페어는 **노드 2 + 엣지 1을 한 번에** 만들고(따로 저장 금지), `--by`로 누가 반응했는지(엣지 방향)를 정합니다. 인자 순서는 항상 (owner 발화, ai 발화)이고 owner는 **자연어 원문 그대로**:

```bash
# 내가 먼저 말하고 AI가 반응 → --by ai (기본)
binggu pair "<owner 발화>" "<ai 발화>" --by ai --relation refutes --confirm "PAIR ai_refutes owner:1 ai:1"
# AI가 먼저 말하고 내가 반응 → --by owner
binggu pair "<owner 발화>" "<ai 발화>" --by owner --relation revises --confirm "PAIR owner_revises owner:1 ai:1"
binggu pair "<내 직감만>" --confirm "PAIR owner:1"   # 순수 직감 단독
binggu save "<문장>" --speaker owner   # 단건 저장 + 화자 칸
binggu trust          # 양방향 신뢰도 (누가 더 잘 맞나)
binggu route "<발화>"  # 신규/수정/결과 안내
```

상세: [화자 축 설계](docs/BINGGUPACK_SPEAKER_AXIS_DESIGN.md) · [튜토리얼](docs/BINGGUPACK_TUTORIAL.md).

## Uninstall / rollback

```bash
claude mcp remove "openbinggu-local-sandbox" -s user   # MCP 엔트리 제거
python binggu.py capture uninstall                      # capture profile 완전 제거 (장부 ledger.sqlite는 보존)
```

장부 자체는 로컬 파일이므로, 제거해도 `~/.binggupack/ledger.sqlite`는 보존됩니다.
