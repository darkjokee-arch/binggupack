# BingguPack

**Local-first, evidence-backed memory/context pack framework for AI workflows.**

AI와의 대화에서 남길 가치가 있는 판단·상태·개념을 *후보*로 모으고, **사람이 직접 `SAVE n`을 타이핑한 것만** 로컬 장부에 저장하는 local-first 지식 프레임워크입니다. 자동 저장은 없습니다.

---

## What is BingguPack?

빙구팩은 **빈 뼈대(empty skeleton) 프레임워크**입니다. 코드에는 owner의 데이터도 정답 그래프도 들어있지 않습니다 — 누가 깔아도 똑같이 빈 장부에서 시작해, 자기 기록과 가치관으로 채워 나갑니다.

- **수집은 넓게** — 어느 도구(Claude·ChatGPT·폰·웹)에서 일하든 건질 문장을 후보로 자동 수집.
- **저장은 좁게** — 실제 저장은 사람이 `SAVE n`을 직접 친 것만. **자동 저장 0.**
- **원본은 로컬** — 모든 원본은 내 PC의 단일 `ledger.sqlite`. 외부 서버가 원본을 갖지 않습니다.

## Why it exists

AI와 대화하다 보면 정작 남기고 싶은 것 — 내 판단, 배운 점, 정한 방침 — 이 수십 개 대화창에 흩어져 사라집니다. 그렇다고 전부 자동 저장하면 잡음·민감정보가 쌓이고 통제권을 잃습니다.

**빙구팩의 답: 넓게 줍고, 좁게 저장하고, 확정분만 흐른다 (collect broad, commit narrow, sync confirmed).**

## Stable release

- **Latest stable: v1.10.0** — <https://github.com/darkjokee-arch/binggupack/releases/tag/v1.10.0>
- **Status**: stable · installable MCP package · 3-OS CI verified (ubuntu / macos / windows)
- 변경 이력: [CHANGELOG.md](CHANGELOG.md)

## Core principles

- **Local-first** — 원본은 내 PC `ledger.sqlite` 하나. 클라우드는 원본이 아닙니다.
- **Candidate preview before save** — 모인 후보는 먼저 미리보기(저장 0). `nothing_saved=true`.
- **Human-confirmed SAVE gate** — 키보드로 직접 친 `SAVE n`만 저장. AI는 입력 경로 자체를 못 거쳐 위조 불가.
- **No AI autosave** — `actor=auto`·confirm 누락/불일치·preview 미확인은 전부 BLOCK. AI/reader는 `G4_no_auto`로 차단.
- **Evidence-backed graph grammar** — 5종 노드(문서·증거·개념·상태·판단), 모든 연결에 원문 근거 의무. 검증기가 fail-closed로 강제.
- **OpenCrab Cloud ingest remains HOLD** — owner가 명시 승인하기 전엔 동작하지 않습니다.

## Quick start

```bash
git clone https://github.com/darkjokee-arch/binggupack
cd binggupack
python scripts/openbinggu_doctor.py --selftest      # GATE=GO (write 0)

python binggu.py init --agi-memory                  # 장부 + 전역 후보수집(기본 ON)
python binggu.py capture preview                     # 모인 후보 미리보기 (저장 0)
```

> python 런처는 OS별로: Windows `py` · WSL/macOS/Linux `python3`. 설치·검증 전체 절차는 [INSTALL.md](INSTALL.md).

## Claude Code MCP install

v1.10.0부터 **clone만으로 MCP 서버가 포함**됩니다(`scripts/openbinggu_mcp_server.py`). Claude Code에 sandbox 엔트리로 등록:

```bash
python scripts/smoke_test.py                              # 오프라인 10-check (write 0)
python scripts/install_claude_mcp.py --sandbox --apply    # Claude Code 등록 → 재시작 필요
claude mcp get openbinggu-local-sandbox                   # Connected 확인
```

- `BINGGU_HOME`으로 sandbox/운영 home 분리(미설정 시 `~/.binggupack`).
- 운영 엔트리 `openbinggu-local`은 installer가 건드리지 않습니다(보호).
- 절차 상세: [INSTALL.md](INSTALL.md) · E2E 결과: [docs/BINGGUPACK_MCP_CLEAN_INSTALL_E2E_TEST_REPORT.md](docs/BINGGUPACK_MCP_CLEAN_INSTALL_E2E_TEST_REPORT.md).

## Safety model

빙구팩의 안전 불변식은 약속이 아니라 selftest로 증명됩니다.

- **자동 저장 없음** — 저장은 preview → `SAVE n` 사람 confirm만.
- **사람-발화 저장 게이트(0-A)** — 키보드로 친 `SAVE n`만 사람 승인. AI는 UserPromptSubmit 경로를 못 거쳐 위조 불가.
- **secret/PII hard block** — 시크릿/PII 발화는 후보 단계에서 무조건 제외(정규식 선차단).
- **ledger/active/confirmed 자동 write 0** — 모든 변경 전 스냅샷 + checksum rollback + append-only audit chain.
- **원문 전문 저장 없음** — 고른 문장만 저장. 화면 표시 cap(60~80자)은 표시일 뿐 저장값과 별개.
- pause / resume / uninstall로 언제든 중단·완전 원복.

자세히: [Security & Non-goals](#non-goals--hold).

## What is included in v1.10.0

- **Installable MCP package** — clone → `install_claude_mcp.py` → `claude mcp add` 헬퍼. repo root 자동 감지, `BINGGU_HOME` 격리 주입, 운영 엔트리 보호, Windows `claude.cmd` shim 처리.
- **`scripts/smoke_test.py`** — clone 직후 오프라인 검증. 8 MCP 도구 + save gate(G4_no_auto) + 운영 home 불변 = 10 checks.
- **Cross-platform CI** — ubuntu / macos / windows 3-OS clean-install 자동 검증 (`.github/workflows/mcp-cross-platform-install.yml`).
- 기능 변경은 v1.10.0-rc.1과 동일하며, cross-platform 검증과 MCP tool exposure 게이트를 통과해 stable로 승격했습니다.

## Verification

```bash
python scripts/smoke_test.py                          # 10/10 PASS (MCP 8도구 + save gate + 운영 home 불변)
python scripts/openbinggu_doctor.py --selftest        # GATE=GO (운영 정합, write 0)
python binggu.py --selftest                           # 장부 + capture + hosted 통합
```

각 selftest는 `GATE: GO` + exit 0이면 정상입니다. 전체 검증 목록은 [INSTALL.md](INSTALL.md), 따라하기는 [docs/BINGGUPACK_TUTORIAL.md](docs/BINGGUPACK_TUTORIAL.md).

## Documentation

- [INSTALL.md](INSTALL.md) — 설치·검증·MCP sandbox 등록 실전 절차
- [docs/BINGGUPACK_CAPTURE_HOOK_SETUP.md](docs/BINGGUPACK_CAPTURE_HOOK_SETUP.md) — AGI memory capture 설치/scope/롤백
- [docs/BINGGUPACK_GRAPH_GRAMMAR_SPEC.md](docs/BINGGUPACK_GRAPH_GRAMMAR_SPEC.md) — 증거 기반 그래프 문법
- [docs/BINGGUPACK_CROSS_PLATFORM_SUPPORT.md](docs/BINGGUPACK_CROSS_PLATFORM_SUPPORT.md) — Windows/WSL/macOS 가이드
- [docs/BINGGUPACK_CROSSDEVICE_PUBLISH_PIPELINE_DESIGN.md](docs/BINGGUPACK_CROSSDEVICE_PUBLISH_PIPELINE_DESIGN.md) — PC-mediated read 공유 파이프라인
- [docs/BINGGUPACK_TUTORIAL.md](docs/BINGGUPACK_TUTORIAL.md) — 단계별 따라하기

## Non-goals / HOLD

별도 owner 결정 전까지 동작하지 않는 항목:

- **OpenCrab Cloud** 실 업로드 · Cloud 원본화 · marketplace · 팀/공유/과금. (로컬 역인제스트와 KV 읽기 공유는 구현·라이브 — HOLD 아님.)
- 자동 확정(confirmed) · 사람 SAVE 기록 없는 자동 업로드 · 상주 데몬 · 주기적 자동 pull.
- cos/확률 지표로 capture/save/approve를 결정하는 자동화. semantic_subtype은 표시·추천 보조층일 뿐입니다.

## License

**MIT License** — [LICENSE](LICENSE). Copyright (c) 2026 BingguPack contributors.
