# Phase 3 — Capture 계열 Survey + Risk Map (이관 0)

> Lane A (capture survey 전용). 조사·report·commit만. **이관 0건.**
> worktree: `C:/Users/PC/BingguPack-laneA` / branch `feat/module-cleanup-phase3-capture-survey`

## 0. 요약 (TL;DR)

- capture **module family 핵심 = `scripts/binggu_capture_*.py` 7개** (1511 LOC).
- dependency chain은 단방향 DAG: `classifier`(leaf, 0 dep) → `buffer` → `session` → `cli` / `persist` → `profile`, 그리고 `to_save`(별도 어댑터).
- 위험도: `classifier`/`buffer`/`session`/`cli` = **write 0 / 순수 메모리**. `persist`/`profile` = sqlite/FS write(단 BINGGU_HOME 격리 지원). `to_save` = 운영 save_gate 위임 + sqlite + tempfile.
- 7개 전부 selftest 보유 + **현 시점 7/7 PASS**(BINGGU_HOME 임시격리에서 검증).
- **이관 1순위 추천: `binggu_capture_classifier.py`** — sibling dep 0, write 0, selftest 28케이스 자족, classifier 서브패키지(`binggupack/classifier/`)가 이미 존재(빈 `__init__.py`)해 strangler 착지점 명확.

## 1. capture 계열 파일 목록표

### 1-A. capture module family (이름에 capture + capture 로직 핵심) — **survey 1차 범위**

| 파일 | LOC | 역할 | 직접 import(sibling) | write/risk | selftest |
| :--- | --: | :--- | :--- | :--- | :--- |
| `binggu_capture_classifier.py` | 199 | 발화 판정 순수함수 `classify()` | (없음) | 없음(`re`만) | `_selftest` 28케이스 |
| `binggu_capture_buffer.py` | 114 | 메모리 candidate 버퍼 `CaptureBuffer` | `binggu_capture_classifier` | 없음 | `_selftest` (T1~T11) |
| `binggu_capture_session.py` | 113 | 세션 entrypoint `CaptureSession` | `binggu_capture_buffer` | 없음 | `_selftest` (T1~T7) |
| `binggu_capture_cli.py` | 83 | 무상태 배치 `run_batch()` + CLI | `binggu_capture_session` | 없음(stdin read/stdout만) | `--selftest` (T1~T8) |
| `binggu_capture_persist.py` | 522 | 영속 buffer + scope 게이트 + TTL + rollback | `binggu_capture_classifier`, (lazy)`binggu_semantic_shadow`, `binggu_rationale_suggest`, `openbinggu_label_kind_map` | **sqlite write / FS 플래그 / BINGGU_HOME** | `_selftest` (T1~T23, temp home) |
| `binggu_capture_profile.py` | 316 | init/status/pause/uninstall + settings.json hook 관리 | `binggu_capture_persist` | **settings.json write / FS 플래그** | `_selftest` (T1~T11, temp) |
| `binggu_capture_to_save.py` | 164 | capture 후보 → 저장 게이트 어댑터 | `openbinggu_conversation_candidate_save`, (selftest)`binggu_capture_buffer`·`openbinggu_deprecate_and_remind_g3`·`openbinggu_staging_write_selftest` | **save_gate 위임(sqlite) / tempfile / shutil** | `_selftest` (T1~T13, temp ledger) |

### 1-B. capture 로직 포함 인접 파일(이름 다름) — **survey 범위 밖, 참고만**

| 파일 | capture 관련성 | 비고 |
| :--- | :--- | :--- |
| `openbinggu_conversation_capture_preview.py` | conversation_capture_preview 로컬 구현(순수, write 0). MAX_NODE_SENTENCE=1000 정합 | capture family와 별 라인(MCP preview). 이관 시 별도 lane 권장 |
| `openbinggu_mcp_server_handlers.py` | MCP `capture_preview`/`capture_classify` 핸들러(capture 16회) | MCP family — capture family 동시수정 금지 대상 |
| `watcher_capture_mvp1.py` | 구 watcher capture MVP(capture 9회) | legacy watcher family |
| `openbinggu_phase6_manual_capture_selftest.py` | manual capture e2e selftest(capture 30회) | 테스트 파일 |
| `watcher_op_m0.py` | capture 15회 | watcher family |

> 1-B는 import/리스크 별 라인. **capture family(1-A) 이관 시 1-B와 동시수정 금지**(공통 규약: 같은 module family 동시수정 금지).

## 2. Sibling Dependency Chain 도식

### 2-A. capture family 내부 (단방향 DAG, 순환 0)

```
binggu_capture_classifier   (leaf · sibling dep 0 · write 0)
        ▲                    ▲
        │                    │
binggu_capture_buffer        binggu_capture_persist ── lazy ──► binggu_semantic_shadow
        ▲                            ▲                          binggu_rationale_suggest
        │                            │                          openbinggu_label_kind_map
binggu_capture_session       binggu_capture_profile
        ▲
        │
binggu_capture_cli

binggu_capture_to_save  ── runtime ──► openbinggu_conversation_candidate_save (save_gate 계열)
                        ── selftest ──► binggu_capture_buffer
                                        openbinggu_deprecate_and_remind_g3
                                        openbinggu_staging_write_selftest
```

핵심:
- **classifier가 전 chain의 leaf**(import 0). buffer/persist 둘 다 classifier에만 의존.
- session→buffer→classifier (cli가 session 위 한 겹 더).
- persist→classifier(런타임) + 3개 보조모듈(lazy, preview opt-in 경로에서만).
- profile→persist (그래서 profile은 persist의 전 chain을 간접 의존).
- to_save는 chain 밖 어댑터: 런타임은 save_gate 계열(`openbinggu_conversation_candidate_save`)로만, capture family는 selftest에서만 buffer를 씀.

### 2-B. 외부(capture family로) 들어오는 의존 — 이관 시 영향면

```
scripts/binggu_capture_profile.py  ← (없음 / binggu.py CLI가 함수 호출 추정)
binggu_capture_persist.py          ← binggu_capture_profile
binggu_capture_classifier.py       ← binggu_capture_buffer, binggu_capture_persist  (consumer 2)
```

> classifier를 옮기면 **buffer + persist 두 consumer**가 import 경로 영향(2-A). 그래서 classifier 이관 시 wrapper(re-export)를 반드시 둬야 `from binggu_capture_classifier import classify`가 양형태로 깨지지 않음.

## 3. 위험토큰 매트릭스

스캔 토큰: `os.environ` `BINGGU_HOME` `open(` `sqlite3` `urllib`/`requests`/`socket` `subprocess` `chdir` `sys.path` `shutil`/`tempfile` `.write_text`/`.write_bytes`/`unlink` `.execute(`

| 파일 | env | BINGGU_HOME | sqlite/execute | FS write(write_text/bytes/unlink) | tempfile/shutil | sys.path | subprocess/network/chdir | 위험등급 |
| :--- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| classifier | – | – | – | – | – | – | – | **GREEN** (순수) |
| buffer | – | – | – | – | – | – | – | **GREEN** (순수) |
| session | – | – | – | – | – | – | – | **GREEN** (순수) |
| cli | – | – | – | – | – | – | – | **GREEN** (stdin/stdout만) |
| persist | ✔(`BINGGU_HOME` read) | ✔ | ✔ sqlite3 + execute | ✔ 플래그/buffer.sqlite | ✔ shutil.copy2 + (selftest)tempfile | – | – | **YELLOW** (home 격리됨) |
| profile | – | 간접(persist 경유) | – | ✔ settings.json/플래그 | ✔(selftest tempfile/shutil) | – | – | **YELLOW** (settings write) |
| to_save | ✔(`os.path`) | – | ✔(save_gate 위임 sqlite) | – | ✔ tempfile.mkdtemp + shutil.rmtree | ✔ `sys.path.insert` | – | **YELLOW** (운영 save_gate 위임) |

토큰 카운트(grep): profile=20, persist=27, to_save=9 / 나머지 3+1개=0.

핵심 안전 관측:
- **network 토큰(urllib/requests/socket) 0건** — capture family 전체.
- **subprocess / os.chdir 0건** — 전체.
- persist의 모든 경로 write는 `binggu_home()`(BINGGU_HOME env / home 인자)로 격리 → selftest는 temp home, 운영 `~/.binggupack/ledger.sqlite` **미접촉**(T13/T18/T23로 자기증명).
- to_save는 confirm/actor/preview_id 게이트를 **생성하지 않고 인자 전달만**(자동저장 구조적 불가) — save_gate 변경 금지 규약과 합치. 단 런타임에 `openbinggu_conversation_candidate_save.save_selected`를 호출 → **운영 save_gate에 직결**되므로 이관 우선순위 최후.

## 4. 테스트(selftest) 유무 및 실측

| 파일 | selftest 진입점 | 격리 | 실측(BINGGU_HOME 임시격리) |
| :--- | :--- | :--- | :--- |
| classifier | `__main__`→`_selftest` (28 case) | 불필요(순수) | **PASS** rc=0 |
| buffer | `__main__`→`_selftest` | 불필요(순수) | **PASS** rc=0 |
| session | `__main__`→`_selftest` | 불필요(순수) | **PASS** rc=0 |
| cli | `--selftest`→`_selftest` | 불필요 | **PASS** |
| persist | `__main__`→`_selftest` (T1~T23) | temp home 내부 생성 | **PASS** |
| profile | `__main__`→`_selftest` (T1~T11) | temp home + temp settings | **PASS** |
| to_save | `__main__`→`_selftest` (T1~T13) | temp ledger + 운영 mtime 검증 | **PASS** |

> 7/7 PASS. 검증 명령: `export BINGGU_HOME="$(mktemp -d)/iso_home"` 후 각 모듈 직접 실행. 운영 home 미접촉(persist/profile/to_save는 selftest 내부에서 자체 tempdir 사용).

## 5. 이관 가능 최소 단위 후보 + 근거

### ★ 추천 1순위: `binggu_capture_classifier.py` → `binggupack/classifier/capture_classifier.py`

근거:
1. **sibling dependency 최소(0)** — chain의 leaf. import는 표준 `re` 뿐. 옮겨도 끌려오는 모듈 없음.
2. **write/risk 0** — 위험토큰 0(GREEN). env·sqlite·FS·network·subprocess 전무 → BINGGU_HOME 격리조차 불필요.
3. **테스트 명확** — `_selftest` 28케이스가 모듈 자족(외부 fixture 0). characterization test로 그대로 재사용 가능.
4. **착지점 존재** — `binggupack/classifier/__init__.py`가 이미 있음(현재 빈 패키지). strangler 착지 명확.
5. **wrapper 형태 단순** — phase1/2 패턴대로 `scripts/binggu_capture_classifier.py`를 `sys.path.insert(0, ROOT)` + `from binggupack.classifier.capture_classifier import *` + 명시 re-export(`classify`, 그리고 selftest에서 쓰는 `_hits`/`_any`/패턴 상수 등 밑줄 포함 전 심볼) + `__main__` 유지로 변환. consumer 2개(buffer·persist)의 `from binggu_capture_classifier import classify`가 wrapper로 그대로 동작.

이관 절차(실행은 다음 Lane/단계 — **본 lane에서 금지**):
- characterization: 현 `_selftest` 28케이스 PASS를 baseline으로 고정.
- 모듈 본체를 `binggupack/classifier/capture_classifier.py`로 이동 + `binggupack/classifier/__init__.py`에서 re-export.
- `scripts/binggu_capture_classifier.py`를 wrapper로 치환(전 심볼 re-export + `__main__`).
- wrapper import(`from binggu_capture_classifier import classify`)와 package import(`from binggupack.classifier.capture_classifier import classify`) **양형태 재현** 확인.
- consumer(buffer/persist) selftest 회귀 + classifier selftest 재실행.

### 차순위(참고, 추천 아님)
- `binggu_capture_buffer.py`(차2): classifier 이관 후에야 깔끔(buffer→classifier 의존). 단독 이관 시 classifier wrapper 선행 필요.
- `binggu_capture_persist.py`/`profile.py`/`to_save.py`: write·save_gate 위임으로 **최후**. to_save는 save_gate 변경 금지 규약에 가장 근접 → 가장 보수적으로.

## 6. Risk Map (이관 관점)

| 등급 | 파일 | 이관 리스크 요인 | 완화책 |
| :--- | :--- | :--- | :--- |
| LOW | classifier, buffer, session, cli | 순수 메모리·write 0 | wrapper re-export로 consumer 경로 보존(classifier는 consumer 2) |
| MED | persist | sqlite/FS write(단 BINGGU_HOME 격리) · 보조모듈 3개 lazy 의존 | 이관 시 lazy import 경로(`binggu_semantic_shadow` 등) 동시 보존 필요 / selftest temp home 강제 |
| MED | profile | settings.json 실편집(register/unregister hook) · persist 전체 의존 | settings write는 호출자 경로 한정 / persist 이관 선행 후 진행 |
| HIGH | to_save | 런타임 `save_selected`(운영 save_gate) 직결 · 자동저장 게이트 인접 | **save_gate 변경 금지 규약**: 어댑터는 인자 전달만 — 이관해도 게이트 로직 불변 보장, 가장 마지막 |

### 동시수정 금지 경계(공통 규약)
- capture family(1-A) ↔ MCP family(`openbinggu_mcp_server_handlers.py`) ↔ watcher family(`watcher_*`) ↔ save_gate family(`openbinggu_conversation_candidate_save.py`) 는 **서로 다른 module family** → phase3에서 동시수정 금지.
- to_save가 save_gate family를 런타임 의존하므로, to_save 이관은 save_gate lane과 충돌 주의(별 lane·별 phase).

## 7. 본 lane 무결성 선언
- 이관 0건 · 코드 수정 0건 · push 0 · 운영 `~/.binggupack` 미접촉 · save_gate/storage resolver/interactive_save 미변경.
- 추가 산출물: 본 문서 1개(`docs/PHASE3_CAPTURE_SURVEY.md`).
- selftest는 BINGGU_HOME 임시격리에서만 실행(읽기·검증 목적), 운영 home write 0.
