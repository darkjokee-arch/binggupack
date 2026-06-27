# BingguPack

**AI와 일하면서 쌓이는 내 판단, 실수, 취향, 규칙을 내 PC 안의 기억 장부로 바꾸는 로컬 우선 AI 작업 메모리입니다.**

> 현재 `main`: 분류 기준 통합, `remember`/`pair` 명시 입력 경로, 회상 효용 trace, `storage`/`mcp` facade까지 반영.
> 최신 배포판: **v1.15.0** · [Release](https://github.com/darkjokee-arch/binggupack/releases/tag/v1.15.0) · [PyPI](https://pypi.org/project/binggupack/)
> 로컬 우선 · 자동 저장 없음 · 내가 고른 것만 저장 · MIT License

[처음 시작하기](docs/START_HERE.md) · [10분 튜토리얼](docs/BINGGUPACK_TUTORIAL.md) · [설치](INSTALL.md) · [Claude Code MCP](INSTALL.md#install-claude-code-mcp-sandbox-entry) · [캡처 hook](docs/BINGGUPACK_CAPTURE_HOOK_SETUP.md) · [문서 색인](docs/INDEX.md)

AI와 오래 일하다 보면 이런 일이 반복됩니다.

- 매번 "나는 짧게 답하는 걸 좋아한다"고 다시 설명합니다.
- 예전에 터졌던 실수를 다른 작업에서 또 조심해야 합니다.
- AI가 맞았는지, 내 직감이 맞았는지 시간이 지나면 흐려집니다.
- 다른 기기에서 떠오른 메모를 나중에 내 작업 장부로 옮기고 싶습니다.
- 자동 저장은 편하지만, 내 대화와 민감정보가 마음대로 쌓이는 건 싫습니다.

BingguPack은 모든 대화를 긁어모으는 도구가 아닙니다.
다음에도 써먹을 **판단·교훈·선호·규칙**만 후보로 보여주고, 내가 직접 고른 것만 로컬 장부(`ledger.sqlite`)에 남깁니다.

## 30초 흐름

```text
AI와 작업하다가 "이건 다음에도 기억해야겠다"는 말이 생김
  -> BingguPack이 저장 후보로 미리 보여줌
  -> 내가 번호를 고르고 SAVE n으로 승인
  -> 내 PC의 ledger.sqlite에 저장
  -> 다음 작업 시작 전에 관련 기억과 과거 실수 패턴을 다시 보여줌
```

## 기능 전체 지도

아래는 현재 저장소 기준으로 BingguPack이 가진 기능 표면입니다.
일반 사용자는 위에서부터 보면 되고, 아래로 갈수록 Claude Code 연결·폰/클라우드·개발자 검증 기능입니다.

### 1. 개인 기억 장부

| 기능 | 무엇을 해주나 | 명령·구성 |
|---|---|---|
| 장부 만들기 | 내 PC에 로컬 기억 장부와 snapshot 폴더를 만듭니다. | `start`, `init` |
| 상태 점검 | 장부, 환경, 저장 게이트, 선택 기능 상태를 확인합니다. | `doctor`, `status` |
| 저장 후보 보기 | 문장을 바로 저장하지 않고 후보로만 보여줍니다. | `remember`, `preview` |
| 회고를 지식 후보로 바꾸기 | 자가평가·회고·작업 후 교훈을 다음에 쓸 기억 후보로 바꿉니다. | `reflect`, `reflect --from-file` |
| 사람 승인 저장 | preview를 본 뒤 고른 번호만 저장합니다. | `save --preview-id ... --pick ... --confirm "SAVE n"` |
| 대화 화자 보존 | 내 말(owner)과 AI 말(ai)을 구분해 저장합니다. | `save --speaker owner/ai`, `pair` |
| 목록 보기 | 저장된 후보·active 기억을 상태와 종류별로 봅니다. | `list --status`, `list --kind` |

### 2. 작업 전 회상과 설명

| 기능 | 무엇을 해주나 | 명령·구성 |
|---|---|---|
| 관련 기억 찾기 | 지금 질문과 비슷한 과거 판단·교훈을 찾아줍니다. | `ask`, `recall`, `why` |
| 작업 시작 전 경고 | 작업 전에 관련 기억, 위험 패턴, 조심할 점을 먼저 보여줍니다. | `preflight --prompt ...` |
| 자동 preflight | Claude Code 발화 전에 preflight를 자동으로 띄웁니다. | `preflight --install`, `--enable`, `--disable` |
| 회상 근거 보기 | 왜 이 기억이 떠올랐는지 근거 사슬을 봅니다. | `trace show <node_id>` |
| 회상 품질 기록 | 떠올린 기억이 실제로 도움이 됐는지 표시합니다. | `trace enable`, `trace review`, `trace mark used/ignored/corrected` |

### 3. 기억 정리와 생명주기

| 기능 | 무엇을 해주나 | 명령·구성 |
|---|---|---|
| 오래된 기억 폐기 | 틀렸거나 더 이상 쓰지 않을 기억을 deprecated로 내립니다. | `deprecate <n> <id8> --reason ... --confirm ...` |
| 새 기억으로 교체 | 기존 기억을 더 나은 문장으로 교체하고 연결을 남깁니다. | `replace <n> <id8> --with ...` |
| 사람 확정 표시 | 내 판단으로 받아들인 기억을 표시하거나 해제합니다. | `accept`, `unaccept` |
| 나중에 다시 보기 | 검증이 필요한 판단에 재검토 날짜를 붙입니다. | `due --date YYYY-MM-DD`, `reminders` |
| 결과 기록 | 나중에 성공·실패·불확실·판정불가로 정리합니다. | `resolve --outcome ...` |
| 저장 의도 안내 | 새 저장인지, 수정인지, 결과 기록인지 명령을 안내합니다. | `route` |

### 4. 내 판단과 AI 판단 비교

| 기능 | 무엇을 해주나 | 명령·구성 |
|---|---|---|
| 내 말과 AI 말 묶기 | owner 발화와 AI 요약을 각각 노드로 저장하고 관계 엣지로 묶습니다. | `pair "<내 말>" "<AI 말>"` |
| 반응 방향 보존 | AI가 내 말을 수용/반박/수정했는지, 내가 AI 말을 수용/반박/수정했는지 남깁니다. | `--by ai/owner`, `--relation accepts/refutes/revises` |
| 순수 직감 저장 | AI 말 없이 내 직감만 owner 노드로 남깁니다. | `pair "<내 직감>" --confirm "PAIR owner:1"` |
| 적중률 보기 | 내 직감과 AI 의견이 나중에 얼마나 맞았는지 참고 지표로 봅니다. | `trust`, `resolve` 연동 |

### 5. Claude Code hook과 MCP 연결

| 기능 | 무엇을 해주나 | 명령·구성 |
|---|---|---|
| 발화 후보 수집 | Claude Code 대화 중 판단·교훈 후보만 버퍼에 모읍니다. 자동 저장은 아닙니다. | `capture enable`, `capture preview` |
| 수집 제어 | 잠깐 끄기, 재개, 영구 OFF, 완전 제거를 지원합니다. | `capture pause/resume/disable/enable/uninstall` |
| SAVE 발화 게이트 | 사람이 `SAVE n`이라고 말한 경우만 저장 게이트 증거로 기록합니다. | `capture install-gate`, `uninstall-gate` |
| Claude Code MCP 서버 | Claude Code에서 BingguPack 도구를 stdio MCP로 연결합니다. | `scripts/openbinggu_mcp_server.py`, `mcp.example.json` |
| MCP 설치 도우미 | sandbox home을 분리해 안전하게 MCP 엔트리를 등록합니다. | `scripts/install_claude_mcp.py --sandbox --apply` |
| MCP 8도구 | preview, classify, pack build/validate, publish guard, consumer smoke, save candidate, selftest를 노출합니다. | `selftest`, `capture_classify`, `capture_preview`, `pack_build`, `pack_validate`, `publish_guard_dryrun`, `consumer_smoke`, `save_candidate` |
| MCP 저장 안전장치 | MCP 단독 저장은 `G4_no_auto`로 차단하고 temp ledger만 씁니다. | actor=reader, `ledger=temp_only` |

### 6. 폰·웹·클라우드 보조 경로

| 기능 | 무엇을 해주나 | 명령·구성 |
|---|---|---|
| hosted inbox 보기 | 다른 기기에서 보낸 저장 의도를 로컬에서 읽기 전용으로 확인합니다. | `hosted inbox` |
| 선택 항목만 가져오기 | inbox에서 고른 번호만 로컬 장부로 가져옵니다. | `hosted pull --select ... --confirm "LIVE SAVE ..."` |
| Cloudflare Worker skeleton | save-intent, capture preview, hosted MCP 실험 경로를 제공합니다. | `hosted/workers/*` |
| cloud 설정 오케스트레이션 | KV, wrangler 설정, 배포 전 점검을 한 진입점에서 수행합니다. | `setup-cloud`, `setup-cloud --apply`, `--deploy` |
| 신뢰 경계 | TTL, pull 후 purge, HMAC, 원문 최소화 정책을 검증합니다. | `tests/hosted_boundary_e2e.py` |

### 7. 외부 소스와 그래프

| 기능 | 무엇을 해주나 | 명령·구성 |
|---|---|---|
| 외부 소스 등록 | arxiv, GitHub, RSS, URL 등 사람이 고른 소스만 화이트리스트에 등록합니다. | `harvest add --kind ... --url ...` |
| 후보 수확 | 등록된 소스를 읽어 후보로만 올립니다. 영구 저장은 사람이 다시 승인합니다. | `harvest run`, `harvest list`, `harvest remove` |
| 관계 후보 확인 | 기억 사이의 관계 후보를 보여주고 사람이 승인/거절합니다. | `confirm-edges --approve`, `--reject` |
| 그래프 문법 | 노드·동사형 엣지·evidence 연결 규칙을 검증합니다. | `binggupack/schema/verb_edge.py`, `docs/BINGGUPACK_GRAPH_GRAMMAR_SPEC.md` |
| pack 계약 | 기억 pack을 외부로 주고받을 때 지켜야 하는 JSON 형식을 검증합니다. | `schemas/openbinggu_pack_contract.schema.json`, `openbinggu_pack_validate.py` |
| 로컬 ingest | incoming 폴더나 staging 데이터를 읽어 검토 가능한 후보로 바꿉니다. | `localbinggu_incoming_loader.py`, `localbinggu_ingest_executor.py` |
| watcher 계열 | 파일·incoming·edge·pack 후보를 감시하고 배치 후보를 만드는 내부 자동화 경로입니다. | `watcher_*`, `watcher_incoming_folder_adapter.py` |

### 8. 안전·검증·개발자 기능

| 기능 | 무엇을 해주나 | 명령·구성 |
|---|---|---|
| 경로 안전 게이트 | MCP/local 도구가 허용 루트 밖을 읽거나 쓰지 못하게 막습니다. | `binggupack/safety/path_safety.py`, `openbinggu_path_safety_gate.py` |
| 민감정보 저장 방지 | SAVE 게이트, PII/secret 차단, hash 기반 확인 기록을 제공합니다. | `binggupack/safety/gate_*`, `binggu_save_gate.py` |
| 의미 분류 | 판단·교훈·선호·규칙 후보를 정규식/semantic 경로로 분류합니다. | `binggupack/classifier/*`, `binggu_canonical_semantic.py` |
| match policy | 중복·유사 후보를 자동 병합할지, 리뷰 후보로 내릴지 정책으로 분류합니다. | `binggupack/policy/match.py`, `localbinggu_match_policy.py` |
| OS별 경로 처리 | Windows, WSL, macOS, Linux의 home/ledger/settings 경로를 정리합니다. | `binggupack/workspace/platform.py` |
| 패키지 실행 | 설치 후 CLI와 모듈 실행을 모두 지원합니다. | `binggu`, `binggupack`, `python -m binggupack` |
| 대화형 저장 보조 | TTY에서 후보 선택과 confirm 문구 구성을 도와줍니다. | `python -m binggupack.cli.interactive_save` |
| 세션 종료 후보화 | 세션 마무리 시 남길 만한 교훈을 후보로 정리하는 경로가 있습니다. | `binggu_session_close.py` |
| 자기진화 거버넌스 | 학습 결과와 기존 규칙이 충돌할 때 자동 변경하지 않고 대비표·적중률·무결성 증거를 남깁니다. | `binggu_contrast_protocol.py`, `binggu_hit_stats.py`, `binggu_merkle_anchor.py`, `binggu_policy.py` |
| smoke/selftest | 장부, MCP, hosted, storage, classifier, pack, publish 경로를 회귀 검증합니다. | `python binggu.py --selftest`, `scripts/smoke_test.py`, `tests/*` |
| publish/export 파이프라인 | pack export, hit export, publish queue, OpenCrab pack 생성 등 고급 배포 준비 경로가 있습니다. | `scripts/binggu_publish_*`, `binggu_cloud_pack_export.py`, `binggu_hit_export.py` |
| reviewed plan 검증 | 사람이 검토한 계획만 적용 가능한지 미리보기와 가드를 검증합니다. | `binggupack/review/*`, `openbinggu_reviewed_*` |
| hybrid AGI 실험 모듈 | commit-reveal, blind ledger, drift metrics, sync adapter 등 실험적 고급 모듈이 들어 있습니다. | `scripts/hybrid_agi/*` |

### CLI 명령 전체 목록

```text
장부/상태:        init(start), status(doctor), list
후보/저장:        preview(remember), reflect, save
회상/설명:        recall(ask), why, preflight, trace
정리/검토:        deprecate, replace, accept, unaccept, due, reminders, resolve
화자/신뢰:        pair, trust, route
Claude hook:      capture status/pause/resume/disable/enable/preview/uninstall/install-gate/uninstall-gate
폰/클라우드:      hosted inbox, hosted pull, setup-cloud
외부/그래프:      harvest add/list/remove/run, confirm-edges
```

## 핵심 원칙

- **내 PC가 정본입니다.** 기본 장부는 로컬 `ledger.sqlite`입니다.
- **자동 영구 저장은 없습니다.** AI, hook, hosted 경로는 후보를 만들 수 있을 뿐입니다.
- **민감정보는 후보 단계에서 차단합니다.** 비밀번호·개인정보를 기억으로 쌓지 않는 쪽이 기본값입니다.
- **클라우드는 보조 경로입니다.** hosted inbox는 잠깐 받아두는 통로이고, 원본 기억을 맡기는 서비스가 아닙니다.
- **기억도 고칠 수 있습니다.** 오래된 판단은 교체, 폐기, 재검토할 수 있습니다.

### 처음이라면 여기부터

**[docs/START_HERE.md](docs/START_HERE.md)** 에서 5분 흐름만 따라가면 됩니다.

## 빙구팩이 아닌 것

범위를 일부러 좁게 잡았습니다.

- 문서 검색용 RAG가 아닙니다.
- 팀 위키가 아닙니다.
- 모든 대화를 자동으로 수집하는 도구가 아닙니다.
- 클라우드에 원본 기억을 맡기는 서비스가 아닙니다.

핵심은 **내 판단을 내가 승인해서 남기고, 다음 작업 전에 꺼내 쓰는 것**입니다.

## 5분 사용법

clone해서 바로 쓸 수 있습니다. 아래에서 `binggu`라고 부르는 명령은 clone한 폴더에서는 `python binggu.py`로 실행하면 됩니다.

일반 사용자는 PyPI 설치가 가장 짧습니다.

```bash
pip install binggupack
binggu start
binggu remember "배포 전에 live endpoint를 먼저 확인한다"
binggu doctor
```

`python -m binggupack doctor`처럼 모듈 실행도 됩니다.

소스에서 바로 실행하려면 clone해서 `python binggu.py`를 쓰면 됩니다.

```bash
git clone https://github.com/darkjokee-arch/binggupack.git
cd binggupack
python binggu.py start
```

내가 기억하고 싶은 말을 미리 봅니다. 이 단계에서는 저장되지 않습니다.

```bash
python binggu.py remember "배포 전에 live endpoint를 먼저 확인한다"
```

출력에 저장 명령이 같이 나옵니다. 그 명령을 보고 내가 고른 번호만 저장합니다.

처음 보는 문서가 많다면 [START_HERE](docs/START_HERE.md) → [10분 튜토리얼](docs/BINGGUPACK_TUTORIAL.md) → [설치 가이드](INSTALL.md)까지만 보면 됩니다.

```bash
python binggu.py save "배포 전에 live endpoint를 먼저 확인한다" \
  --preview-id <화면에 나온 id> --pick 1 --explicit --confirm "SAVE 1"
```

다음 작업 전에 관련 기억을 불러옵니다.

```bash
python binggu.py ask "배포 전에 조심할 것?"
```

상태 점검은 이렇게 합니다.

```bash
python binggu.py doctor
```

## 자주 쓰는 명령

| 하고 싶은 일 | 명령 | 뜻 |
|---|---|---|
| 처음 시작 | `python binggu.py start` | 내 장부 만들기 |
| 기억 후보 보기 | `python binggu.py remember "..."` | 저장 전 미리보기 |
| 관련 기억 찾기 | `python binggu.py ask "..."` | 회상 |
| 상태 점검 | `python binggu.py doctor` | 장부·환경 확인 |
| 내 말과 AI 의견 묶기 | `python binggu.py pair "내 말" "AI 말" ...` | 누가 맞았는지 나중에 비교 |
| 회상이 도움됐는지 표시 | `python binggu.py trace review` / `trace mark` | 효용 기록(opt-in) |

## 무엇이 저장 후보가 되나요

자동 캡처와 미리보기는 같은 기준을 씁니다.
아무 문장이나 저장 후보로 올리지 않습니다.

| 후보가 됨 | 후보가 안 됨 |
|---|---|
| 판단: "이건 B안으로 간다" | 조회: "상태 보여줘" |
| 교훈: "다음부터 백업 먼저" | 일회성 지시: "지금 이거 고쳐" |
| 선호: "나는 짧은 답을 선호한다" | 운영 보고: "커밋 완료" |
| 규칙: "배포는 두 번 확인" | 잡담: "오늘 수고했어" |
| 위험 감지: "이 방식은 터질 수 있다" | 순수 지식: "토큰 버킷은 ..." |

`remember`와 `pair`처럼 사용자가 직접 "이걸 기억하라"고 입력한 경로는 조금 더 넓게 받습니다.
그래도 비밀번호·개인정보·자동 저장 차단 같은 안전 게이트는 그대로 유지됩니다.

## 어떻게 데이터가 움직이나요

```text
내 문장
  -> preview/remember 에서 후보 확인
  -> 내가 번호를 골라 confirm
  -> 내 PC의 ledger.sqlite 에 저장
  -> ask/preflight 가 다음 작업 전에 다시 보여줌
```

원본 기억의 기준점은 로컬 장부입니다.
외부 도구나 hosted inbox는 보조 경로이고, 원본을 대신하지 않습니다.

## 안전 약속

- **자동 저장 없음**: AI나 자동 경로는 durable 저장을 못 합니다.
- **사람 승인 필요**: 저장은 내가 번호를 고르고 confirm할 때만 됩니다.
- **민감정보 차단**: 비밀번호·개인정보는 후보 단계에서 제외합니다.
- **원본은 내 PC**: 기본 장부는 로컬 `ledger.sqlite`입니다.
- **되돌릴 수 있음**: 저장 전 snapshot과 audit log를 남깁니다.
- **회상 효용 trace도 opt-in**: 켜야만 기록되고, 원문 대신 node_id·분류·점수 같은 메타데이터만 저장합니다.

## 조금 더 깊게 쓰기

| 주제 | 문서 |
|---|---|
| 처음 따라하기 | [START_HERE](docs/START_HERE.md) |
| 10분 튜토리얼 | [BINGGUPACK_TUTORIAL](docs/BINGGUPACK_TUTORIAL.md) |
| 설치 | [INSTALL](INSTALL.md) |
| 내 말 / AI 말 따로 저장 | [BINGGUPACK_SPEAKER_AXIS_DESIGN](docs/BINGGUPACK_SPEAKER_AXIS_DESIGN.md) |
| 사람 승인과 안전 경계 | [BINGGUPACK_GOVERNANCE_DESIGN](docs/BINGGUPACK_GOVERNANCE_DESIGN.md) |
| hosted inbox 경계 | [BINGGUPACK_HOSTED_BOUNDARY](docs/BINGGUPACK_HOSTED_BOUNDARY.md) |
| 팩 형식 | [OPENBINGGU_PACK_CONTRACT](docs/OPENBINGGU_PACK_CONTRACT.md) |
| 전체 문서 색인 | [docs/INDEX.md](docs/INDEX.md) |

## 개발자용 짧은 점검

```bash
python scripts/openbinggu_doctor.py --selftest
python scripts/smoke_test.py --home ./_binggu_smoke_home
python binggu.py --selftest
```

문서와 스크립트 이름에 남아 있는 `OpenBinggu`는 예전 내부 코드네임입니다. 현재 공개 제품명은 BingguPack이고, 새로 읽을 문서는 `BINGGUPACK_*`와 `START_HERE`를 우선 보세요.

## License

MIT License — Copyright (c) 2026 BingguPack contributors.
