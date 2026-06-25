# Install BingguPack v1.11.0

> Latest stable: `v1.11.0` (v1.10.0 installable MCP baseline 위 feature implementation). 설치/검증 절차는 v1.10.0과 동일하게 호환됩니다 — 아래 명령 그대로 사용하세요. PyPI publish는 아직 하지 않으므로 `git clone` 설치만 지원합니다.

> `scripts/`·`docs/`의 `openbinggu_`/`OPENBINGGU_` 접두사는 레거시 내부 코드네임입니다(BingguPack과 동일 프로젝트).

이 문서는 **실전 설치 절차** 중심입니다. 개념·설계는 [README.md](README.md), 따라하기는 [docs/BINGGUPACK_TUTORIAL.md](docs/BINGGUPACK_TUTORIAL.md)를 보세요.

설치 흐름: **Requirements → Clone → Verify → MCP sandbox 등록 → 재시작 → 도구 확인 → save gate 확인 → 운영 home 보호 → Troubleshooting**.

---

## Requirements

- **Python 3.10+** (표준 라이브러리 위주, 외부 런타임 의존성 0)
- **git** — 없으면 GitHub **Code → Download ZIP**으로 받아 압축 해제해도 됩니다.
- **Claude Code** — MCP sandbox 등록을 하려면 필요(`claude` CLI).
- OS: Windows / WSL / macOS / Linux — 같은 정책으로 동작.
- python 런처: Windows `py` · WSL/macOS/Linux `python3`. 아래 예시의 `python`을 OS에 맞게 바꿔 쓰세요.
- (선택) hosted/MCP·semantic 도장: Node.js + `wrangler`, Ollama `bge-m3`. 로컬 CLI만 쓰면 불필요.

## Clone

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
#  기대: 10/10 PASS · save_actual_G4_no_auto_BLOCK PASS · operating_ledger_write_0 PASS

python scripts/openbinggu_doctor.py --selftest                  # GATE=GO (운영 정합, write 0)
python scripts/openbinggu_doctor.py --tree examples/toy_project # CLEAN
```

> 더 깊은 모듈별 selftest(capture · publish · cross-platform · candidate UX 등) 전체 목록은 [docs/BINGGUPACK_TUTORIAL.md](docs/BINGGUPACK_TUTORIAL.md)와 각 스크립트 `--selftest`를 참고하세요.

## Install Claude Code MCP sandbox entry

v1.10.0부터 **clone만으로 MCP 서버가 포함**됩니다(`scripts/openbinggu_mcp_server.py`). sandbox 엔트리로 등록(미리보기 → 실제):

```bash
python scripts/install_claude_mcp.py --sandbox --home ./_binggu_test_home --dry-run   # 명령 미리보기
python scripts/install_claude_mcp.py --sandbox --home ./_binggu_test_home --apply     # 실제 claude mcp add
```

- `BINGGU_HOME`으로 sandbox/운영 home 분리(미설정 시 `~/.binggupack`). installer가 MCP config `env`에 주입합니다.
- 운영 엔트리 `openbinggu-local`은 installer가 건드리지 않습니다(거부). sandbox 이름만 등록됩니다.

## Restart Claude Code

MCP 도구는 **세션 시작 시 고정**됩니다. 등록 후 **Claude Code를 반드시 재시작**해야 도구가 노출됩니다.

## Confirm MCP tools

재시작 후 sandbox 서버 연결과 8도구 노출을 확인합니다:

```bash
claude mcp list
claude mcp get openbinggu-local-sandbox    # Status: Connected · env BINGGU_HOME 확인
```

노출되어야 하는 8 MCP 도구:
`selftest` · `capture_classify` · `capture_preview` · `pack_build` · `pack_validate` · `publish_guard_dryrun` · `consumer_smoke` · `save_candidate`

## Confirm save gate

AI/reader actor의 실저장은 차단되어야 정상입니다. `save_candidate`를 `dry_run=false`로 호출하면:

- **`G4_no_auto` BLOCK** · `executed_write=false` · `ledger=temp_only`

이건 실패가 아니라 PASS입니다. 저장은 오직 사람의 `SAVE n` 승인 게이트에서만 일어납니다.

## Operating home vs sandbox home

- **운영 home** (`~/.binggupack`) — 실제 장부 `ledger.sqlite`. installer/sandbox는 여기를 **건드리지 않습니다**.
- **sandbox/test home** (`--home` 으로 지정) — preview/cache 흔적만 남습니다.
- 분리 원칙: `BINGGU_HOME`을 sandbox 경로로 주입하므로, sandbox MCP 호출이 운영 ledger에 durable write를 남기지 않습니다(smoke_test의 `operating_ledger_write_0`이 강제).

## Troubleshooting

- **도구가 안 보임** — Claude Code를 재시작했는지 확인(도구는 세션 시작 시 고정). `claude mcp get openbinggu-local-sandbox`가 Connected인지 확인.
- **`claude` 명령을 못 찾음 (Windows)** — installer가 `claude.cmd` shim을 `shutil.which`로 처리합니다. PATH에 Claude Code가 있는지 확인.
- **운영 엔트리를 덮어쓰려 함** — installer는 `openbinggu-local`(운영)을 거부합니다. sandbox 이름(`--sandbox`)으로 등록하세요.
- **selftest 실패** — `python scripts/openbinggu_doctor.py --selftest`로 GATE 상태를 먼저 확인. write는 항상 0이므로 운영 데이터 손상 없이 재시도 가능.

## Interactive save gate (optional, v1.11.0)

기존 explicit confirm 방식은 그대로 유지되고, TTY에서 후보 선택을 돕는 보조 UX가 추가됐습니다.

```bash
python -m binggupack.cli.interactive_save            # TTY 대화형 (후보 선택 → confirm phrase 구성)
python -m binggupack.cli.interactive_save --selftest # 비-TTY 검증 (저장 0)
```

- non-TTY(CI/pipe/AI)에서는 **fail-closed** — 기존 explicit confirm 방식이 강제됩니다.
- 마지막 human confirmation과 실제 저장 게이트(`G4_no_auto`)는 기존 경로 그대로. interactive는 ledger에 직접 쓰지 않습니다.

## Developer: package build (optional, v1.11.0)

PyPI publish는 **미수행**입니다. 로컬에서 패키지 build만 확인하려면 격리 venv에서:

```bash
python -m venv .build_venv
.build_venv/Scripts/python -m pip install build   # WSL/macOS: .build_venv/bin/python
.build_venv/Scripts/python -m build                # sdist + wheel 생성 (dist/)
```

- `pyproject.toml`에 `[build-system]` + `binggupack` 패키지 정의가 있습니다.
- build 산출물(`dist/`, `.build_venv/`)은 `.gitignore` 대상입니다.

## Uninstall / rollback

```bash
claude mcp remove "openbinggu-local-sandbox" -s user   # MCP 엔트리 제거
python binggu.py capture uninstall                      # capture profile 완전 제거 (장부 ledger.sqlite는 보존)
```

장부 자체는 로컬 파일이므로, 제거해도 `~/.binggupack/ledger.sqlite`는 보존됩니다.
