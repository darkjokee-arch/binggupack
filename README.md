# BingguPack

**Installable MCP package for Claude Code — local-first, evidence-backed memory with a human-confirmed save gate.**

> **Latest (main): `v1.12.0` — Personal speaker axis 🗣️** · 사용자 발화(owner)와 AI 요약(ai)을 **따로 저장**하고 **수용/반박/수정 엣지**로 연결, **양방향 신뢰도**(내 직감·AI 반박 적중률)까지. CLI `binggu pair`/`trust`/`route` · 운영 ledger 마이그레이션 무손상(`verify_tail_state`) · 헌법 5항 위반 0 → [화자 축 설계](docs/BINGGUPACK_SPEAKER_AXIS_DESIGN.md)
>
> **Latest stable: `v1.11.0`** — feature implementation on the v1.10.0 installable MCP baseline · **external clean-clone + isolated build verified ✅**
> `binggupack/` package modularization · interactive save prototype (non-TTY fail-closed) · pack/workflow examples · sdist/wheel build · `scripts/smoke_test.py` **10/10 PASS** · **MCP 8 tools** · `save_candidate(dry_run=false)` blocked by **`G4_no_auto`**
> 🔒 local-first · preview-first · **no AI autosave** · human `SAVE n` gate · PyPI publish 0
> Release: <https://github.com/darkjokee-arch/binggupack/releases/tag/v1.11.0> · 설치: [INSTALL.md](INSTALL.md) · 종료 기록: [v1.11.0 closure](docs/releases/BINGGUPACK_V1_11_0_RELEASE_CLOSURE.md)
> Previous stable: `v1.10.0` (installable MCP package) — [release](https://github.com/darkjokee-arch/binggupack/releases/tag/v1.10.0) · [closure](docs/releases/BINGGUPACK_V1_10_0_RELEASE_CLOSURE.md)

BingguPack은 AI 대화에서 남길 가치가 있는 판단·상태·개념을 *후보*로 모으고, **사람이 직접 `SAVE n`을 타이핑한 것만** 로컬 장부에 저장하는 local-first 지식 프레임워크입니다. **자동 저장은 없습니다.** v1.10.0부터는 이 전체를 **Claude Code MCP 패키지**로 clone 한 번에 설치할 수 있습니다.

---

## v1.12.0 — Personal speaker axis (latest, main)

사용자 AGI화의 핵심 갭을 메우는 화자 축. 빙구팩이 "AI 작업일지"가 아니라 **사용자 본체**를 쌓게 합니다.

- **화자 구분(speaker)** — owner(사용자 발화: 직감·지적·원인) / ai(AI 요약: 수정·수용·반박)를 각각 **독립 노드**로 저장. 기존 노드는 NULL(비파괴 `ALTER`).
- **페어 + 동사형 엣지** — `binggu pair`로 owner/ai 노드를 한 번에 저장하고 `ai_accepts`/`ai_refutes`/`ai_revises`로 연결. **owner 단독(순수 직감)도 허용** — 억지 AI 노드 안 만듦.
- **양방향 신뢰도** — `binggu trust`. 내 직감 적중률과 AI 반박 적중률을 **별도 분모·시간감쇠(반감기 30일)·표본게이트(N<5 미산정)**로 산정해 함께 표시. 참고 가중치이지 맹종 아님(사용자도 AI도 틀릴 수 있다).
- **자기수정 라우팅** — `binggu route`로 "저장해" 발화를 신규/수정(`replace`)/결과(`resolve`)로 추정해 안내(read-only).
- 검증: selftest 전수 GO · 운영 ledger 마이그레이션 **291노드 무손상**(`verify_tail_state`/`verify_chain` True) · 헌법 5항 위반 0. 상세: [화자 축 설계](docs/BINGGUPACK_SPEAKER_AXIS_DESIGN.md).

> ⚠️ 구현 노트: `store_checksum`은 `speaker` 컬럼을 제외(명시 projection)한다 — 포함하면 `ALTER` 후 기존 audit anchor와 어긋나 `verify_tail_state`가 정상 노드를 변조로 오판. `verify_chain`만으론 못 잡힌다.

## v1.11.0 — Feature implementation

v1.10.0 installable MCP stable baseline 위의 feature implementation release입니다.

- **Phase 1 package modularization** — 새 `binggupack/` 패키지(`cli`/`classifier`/`mcp`/`pack`/`safety`/`workspace`). smoke 로직은 `binggupack/pack/smoke.py`로 이관.
- **backward-compatible** — `scripts/smoke_test.py`·`scripts/install_claude_mcp.py`는 기존대로 동작(wrapper). MCP 8도구 그대로 노출, 기존 명령 무결.
- **interactive save prototype** — `python -m binggupack.cli.interactive_save`. confirm phrase 유지, **non-TTY fail-closed**, ledger write 0(기존 게이트 우회 0).
- **pack/workflow examples** — 4 synthetic 시나리오([examples/pack_workflow](examples/pack_workflow/README.md)), 전부 `ingest_performed=false`.
- **packaging/build readiness** — `pyproject.toml` build-system + 격리 venv sdist/wheel build 검증. (**PyPI publish는 미수행.**)
- 검증: smoke **10/10** · clean-clone · isolated build PASS · `G4_no_auto` 유지 · production write/OpenCrab ingest/G4 bypass **0**. 상세: [v1.11.0 closure](docs/releases/BINGGUPACK_V1_11_0_RELEASE_CLOSURE.md).

## v1.10.0 — Installable MCP package (previous stable)

`git clone` 만으로 MCP 서버(`scripts/openbinggu_mcp_server.py`)가 포함되고, `scripts/install_claude_mcp.py` 헬퍼로 Claude Code에 sandbox 엔트리로 등록됩니다.

```bash
git clone https://github.com/darkjokee-arch/binggupack
cd binggupack
python scripts/smoke_test.py                              # 오프라인 10-check (write 0)
python scripts/install_claude_mcp.py --sandbox --apply    # Claude Code 등록 → 재시작 필요
claude mcp get openbinggu-local-sandbox                   # Connected 확인
```

- `BINGGU_HOME`으로 sandbox/운영 home 분리(미설정 시 `~/.binggupack`). installer가 MCP config `env`에 주입.
- 운영 엔트리 `openbinggu-local`은 installer가 건드리지 않습니다(보호). sandbox 이름만 등록.

**노출되는 MCP 8도구**: `selftest` · `capture_classify` · `capture_preview` · `pack_build` · `pack_validate` · `publish_guard_dryrun` · `consumer_smoke` · `save_candidate`. 위험 도구(실 write/apply/push)는 노출 0.

**External clean-clone verification — PASS ✅**
깨끗한 새 환경에서 외부 사용자 관점으로 재현 검증했습니다(`BINGGUPACK_V1_10_0_EXTERNAL_CLEAN_CLONE_VERIFY_PASS`):

| 단계 | 결과 |
|---|---|
| clone → `v1.10.0` tag checkout | commit `d15e6d6` MATCH |
| `scripts/smoke_test.py` | **10/10 PASS** (`real_home_changed=0`, `production_write=0`) |
| MCP sandbox install (dry-run → apply) | PASS · 운영 entry 미변경 |
| MCP tools exposure | **8/8** (forbidden leak 0) |
| `save_candidate(dry_run=false)` actual save | **BLOCK / `G4_no_auto` / executed_write=false / ledger=temp_only** |
| 운영 home durable write · OpenCrab ingest · G4 bypass | 전부 **0** |

절차 상세: [INSTALL.md](INSTALL.md) · E2E: [docs/BINGGUPACK_MCP_CLEAN_INSTALL_E2E_TEST_REPORT.md](docs/BINGGUPACK_MCP_CLEAN_INSTALL_E2E_TEST_REPORT.md) · 종료 기록: [release closure](docs/releases/BINGGUPACK_V1_10_0_RELEASE_CLOSURE.md).

## Packs and workflows

BingguPack은 개인 memory skeleton일 뿐 아니라, **evidence 기반 pack과 AI workflow를 위한 local-first 준비(preparation) layer**입니다.

전형적 흐름:

`goal → workflow design → required packs → required data → evidence capture → candidate nodes/edges → pack validation → publish guard dry-run → consumer smoke → OpenCrab-ready handoff`

BingguPack은 OpenCrab을 실행/워크플로우 엔진으로서 **대체하지 않습니다**. 대신 workflow가 필요로 하는 pack 데이터를 **준비·검증·문서화·안전점검**합니다:

- 사용자 목표(goal)에 어떤 pack이 필요한지 정의한다
- 각 pack이 필요로 하는 evidence·source data를 식별한다
- 캡처한 evidence를 candidate nodes / edges로 변환한다
- handoff 전에 pack 구조를 검증한다 (`pack_build` → `pack_validate`)
- 외부 시스템이 데이터를 받기 전에 publish guard를 돌린다 (`publish_guard_dryrun`)
- consumer view를 smoke-test해 downstream tool이 안전하게 pack을 쓰게 한다 (`consumer_smoke`)

이 흐름을 지원하는 v1.10.0 MCP 도구: `capture_preview` · `pack_build` · `pack_validate` · `publish_guard_dryrun` · `consumer_smoke` · `save_candidate`. 각 단계는 왜 중요한가 — pack이 production으로 넘어가기 전에 **구조 검증(validate) → 외부 노출 게이트(publish guard) → 소비자 관점 확인(consumer smoke)** 을 거쳐, 깨지거나 증거 없는 pack이 downstream에 도달하지 않게 fail-closed로 막기 때문입니다.

**유료 workflow productization 관점** — pack/workflow는 상품화 가능한 산출물이지만, BingguPack은 **준비·검증·핸드오프 layer**까지만 담당합니다. 실제 실행은 OpenCrab(execution/workflow engine)의 몫입니다. production ingest, OpenCrab Cloud publish, marketplace upload, paid workflow deployment는 **owner가 명시 승인하기 전까지 HOLD**이며, 자동으로 publish·ingest되는 것은 아무것도 없습니다.

모든 pack 데이터는 **candidate-first / evidence-backed**로 유지됩니다.

## Safety model

빙구팩의 안전 불변식은 약속이 아니라 selftest로 증명됩니다.

- **No AI autosave** — 저장은 preview → `SAVE n` 사람 confirm만. `actor=auto`·confirm 누락/불일치·preview 미확인은 전부 BLOCK.
- **Human-confirmed SAVE gate (0-A)** — 키보드로 직접 친 `SAVE n`만 사람 승인. AI는 UserPromptSubmit 경로를 못 거쳐 위조 불가.
- **`G4_no_auto`** — AI/reader actor의 실저장(`save_candidate(dry_run=false)`)은 게이트에서 BLOCK. durable ledger write 0.
- **Preview-first** — 모인 후보는 먼저 미리보기(저장 0, `nothing_saved=true`).
- **secret/PII hard block** — 시크릿/PII 발화는 후보 단계에서 무조건 제외(정규식 선차단).
- **ledger/active/confirmed 자동 write 0** — 모든 변경 전 스냅샷 + checksum rollback + append-only audit chain. pause/resume/uninstall로 완전 원복.
- **원문 전문 저장 없음** — 고른 문장만 저장. 화면 표시 cap(60~80자)은 표시일 뿐 저장값과 별개.

## Quick start (local CLI)

MCP 없이 로컬 CLI로 후보 수집·저장을 쓸 수도 있습니다.

```bash
python scripts/openbinggu_doctor.py --selftest      # GATE=GO (write 0)
python binggu.py init --agi-memory                  # 장부 + 전역 후보수집(기본 ON)
python binggu.py capture preview                     # 모인 후보 미리보기 (저장 0)

# 화자 축 (v1.12.0) — 내 발화와 AI 요약을 따로 쌓고 연결
python binggu.py pair "<내 직감>" "<AI 요약>" --relation refutes --confirm "PAIR ai_refutes owner:1 ai:1"
python binggu.py pair "<내 직감만>" --confirm "PAIR owner:1"   # 순수 직감 단독
python binggu.py trust                               # 양방향 신뢰도 보기 (read-only)
python binggu.py route "<발화>"                       # 신규/수정/결과 안내 (read-only)
```

> python 런처는 OS별로: Windows `py` · WSL/macOS/Linux `python3`. 전체 절차는 [INSTALL.md](INSTALL.md).

## Core principles

- **Local-first** — 원본은 내 PC `ledger.sqlite` 하나. 클라우드는 원본이 아닙니다.
- **Candidate preview before save** — 모인 후보는 먼저 미리보기(저장 0).
- **Human-confirmed SAVE gate** — 키보드로 직접 친 `SAVE n`만 저장.
- **No AI autosave** — AI/reader는 `G4_no_auto`로 차단.
- **Evidence-backed graph grammar** — 5종 노드(문서·증거·개념·상태·판단), 모든 연결에 원문 근거 의무. 검증기가 fail-closed로 강제.
- **Personal speaker axis (v1.12.0)** — 사용자 발화(owner)와 AI 요약(ai)을 따로 저장하고 수용/반박/수정 엣지로 연결. 양방향 신뢰도는 한쪽 편들지 않는 참고 가중치(맹종 아님).
- **OpenCrab Cloud ingest remains HOLD** — owner가 명시 승인하기 전엔 동작하지 않습니다.

## Verification

```bash
python scripts/smoke_test.py                          # 10/10 PASS (MCP 8도구 + save gate + 운영 home 불변)
python scripts/openbinggu_doctor.py --selftest        # GATE=GO (운영 정합, write 0)
python binggu.py --selftest                           # 장부 + capture + hosted 통합
```

각 selftest는 `GATE: GO` + exit 0이면 정상입니다. 전체 검증 목록은 [INSTALL.md](INSTALL.md), 따라하기는 [docs/BINGGUPACK_TUTORIAL.md](docs/BINGGUPACK_TUTORIAL.md).

## Philosophy / Personal ontology model

> v1.10.0은 이 철학을 **설치 가능한 MCP 패키지**로 묶은 것입니다. 아래는 빙구팩이 무엇을 지향하는지에 대한 배경입니다.

빙구팩은 **빈 뼈대(empty skeleton) 프레임워크**입니다. 코드에는 owner의 데이터도 정답 그래프도 들어있지 않습니다 — 누가 깔아도 똑같이 빈 장부에서 시작해, 자기 기록과 가치관으로 채워 나갑니다.

AI와 대화하다 보면 정작 남기고 싶은 것 — 내 판단, 배운 점, 정한 방침 — 이 수십 개 대화창에 흩어져 사라집니다. 그렇다고 전부 자동 저장하면 잡음·민감정보가 쌓이고 통제권을 잃습니다.

**빙구팩의 답: 넓게 줍고, 좁게 저장하고, 확정분만 흐른다 (collect broad, commit narrow, sync confirmed).**

- **수집은 넓게** — 어느 도구(Claude·ChatGPT·폰·웹)에서 일하든 건질 문장을 후보로 자동 수집.
- **저장은 좁게** — 실제 저장은 사람이 `SAVE n`을 직접 친 것만. **자동 저장 0.**
- **원본은 로컬** — 모든 원본은 내 PC의 단일 `ledger.sqlite`. 외부 서버가 원본을 갖지 않습니다.
- **개인 온톨로지** — 각자 자기 user_ontology를 꽂는 자리만 제공하고, 그 내용은 빙구팩이 자동 판정하지 않습니다.

## Documentation

- [INSTALL.md](INSTALL.md) — 설치·검증·MCP sandbox 등록 실전 절차
- [docs/releases/BINGGUPACK_V1_10_0_RELEASE_CLOSURE.md](docs/releases/BINGGUPACK_V1_10_0_RELEASE_CLOSURE.md) — v1.10.0 release closure 기록
- [docs/BINGGUPACK_CAPTURE_HOOK_SETUP.md](docs/BINGGUPACK_CAPTURE_HOOK_SETUP.md) — AGI memory capture 설치/scope/롤백
- [docs/BINGGUPACK_GRAPH_GRAMMAR_SPEC.md](docs/BINGGUPACK_GRAPH_GRAMMAR_SPEC.md) — 증거 기반 그래프 문법
- [docs/BINGGUPACK_SPEAKER_AXIS_DESIGN.md](docs/BINGGUPACK_SPEAKER_AXIS_DESIGN.md) — 화자 축(owner/ai)·페어 엣지·양방향 신뢰도 (v1.12.0)
- [docs/BINGGUPACK_CROSS_PLATFORM_SUPPORT.md](docs/BINGGUPACK_CROSS_PLATFORM_SUPPORT.md) — Windows/WSL/macOS 가이드
- [docs/BINGGUPACK_CROSSDEVICE_PUBLISH_PIPELINE_DESIGN.md](docs/BINGGUPACK_CROSSDEVICE_PUBLISH_PIPELINE_DESIGN.md) — PC-mediated read 공유 파이프라인
- [docs/BINGGUPACK_TUTORIAL.md](docs/BINGGUPACK_TUTORIAL.md) — 단계별 따라하기
- [CHANGELOG.md](CHANGELOG.md) — 변경 이력

## Non-goals / HOLD

별도 owner 결정 전까지 동작하지 않는 항목:

- **OpenCrab Cloud** 실 업로드 · Cloud 원본화 · marketplace · 팀/공유/과금. (로컬 역인제스트와 KV 읽기 공유는 구현·라이브 — HOLD 아님.)
- 자동 확정(confirmed) · 사람 SAVE 기록 없는 자동 업로드 · 상주 데몬 · 주기적 자동 pull.
- cos/확률 지표로 capture/save/approve를 결정하는 자동화. semantic_subtype은 표시·추천 보조층일 뿐입니다.

## License

**MIT License** — [LICENSE](LICENSE). Copyright (c) 2026 BingguPack contributors.
