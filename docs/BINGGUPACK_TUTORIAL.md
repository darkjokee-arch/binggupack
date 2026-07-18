# BingguPack 10분 튜토리얼 — 설치부터 후보 관리(candidate UX)까지

> 이 문서는 BingguPack을 처음 받은 사용자가 **데이터 없이도** 전체 흐름을 따라해 보는 가이드입니다.
> 모든 단계는 로컬 synthetic/temp 데이터만 사용하며, 운영 저장소를 변경하지 않습니다.
> 이 튜토리얼은 **CLI/local 흐름** 기준입니다 — 채팅 앱 경로(hosted custom connector)는 **read-only 6도구**가 Claude·ChatGPT에서 동작 확인되었습니다(배포 방법: `../hosted/workers/README.md`). 채팅에서의 **저장(save-intent)은 v1.2.0부터 동작 검증**되었습니다 — 폰 미리보기→`SAVE n`→PC 러너 pull→로컬 장부 저장 (`../README.md` 상태표·`BINGGUPACK_HOSTED_SAVE_INTENT_DESIGN.md` 참조).

## 0. 준비물

- Python 3.10+ (표준 라이브러리 위주 — 별도 패키지 설치 불필요)
- 외부 네트워크·DB·Neo4j 전부 불필요

## 1. 받기

일반 사용자는 PyPI로 설치합니다.

```bash
python -m pip install binggupack
binggu start
binggu doctor
```

개발/검증 명령까지 직접 돌리려면 clone합니다.

```bash
git clone https://github.com/darkjokee-arch/binggupack.git
cd binggupack
```

## 2. 동작 확인 (doctor)

```bash
python scripts/openbinggu_doctor.py --selftest
```

✅ 기대: 마지막에 `GATE: GO` + 종료코드 `0`.
(버전에 따라 검사 개수는 늘어날 수 있습니다. `GATE: GO`가 아니거나 종료코드가 0이 아닐 때만 사용을 멈추고 이슈를 확인하세요.)

## 3. toy 예제로 pack 흐름 보기

```bash
# 공개 적합성 스캔 (toy 트리는 CLEAN 이어야 정상)
python scripts/openbinggu_doctor.py --tree examples/toy_project

# pack 빌드 → 검증 → 읽기
python scripts/watcher_pack_builder_m0.py --selftest
python scripts/openbinggu_pack_validate.py --selftest
python scripts/openbinggu_pack_consumer_smoke.py --selftest
```

✅ 기대: 각 명령 끝에 `GATE: GO` + 종료코드 `0`.

## 4. batch pack을 내 로컬 staging에 넣어보기

먼저 아무 위험 없는 자동 검증부터:

```bash
python scripts/openbinggu_batch_pack_loader.py --selftest    # 10/10 기대
```

✅ 기대: synthetic pack으로 **apply → read-back → rollback(원복)** 전 과정이 temp에서 자동 검증됩니다. 내 데이터 0.

> **내 pack은 어디서 오나?** pack 생성은 이 repo의 빌더 `scripts/watcher_pack_builder_m0.py <입력 디렉터리>`(3단계에서 selftest로 본 그 스크립트)로 로컬에서 수행하고, BingguPack은 그렇게 만들어진 pack을 **검증·적재·preview**하는 쪽을 담당합니다. 흐름 개요는 [USER_DRIVEN_OPENCRAB_UPLOAD_FLOW](BINGGUPACK_USER_DRIVEN_OPENCRAB_UPLOAD_FLOW.md) 참고. (여기서 OpenCrab apply/finalize/upload를 실행하라는 뜻이 아닙니다 — 그 단계들은 별도 승인 영역이며 이 튜토리얼 범위 밖입니다.)

실제로 내 pack을 넣어보고 싶으면 (**명시 opt-in 필수**, 기본은 rollback 원복):

macOS/Linux (bash):
```bash
OPENBINGGU_HOME=<repo 밖 경로> python scripts/openbinggu_batch_pack_loader.py --pack-dir <pack 디렉터리> --enable-write
```

Windows (PowerShell):
```powershell
$env:OPENBINGGU_HOME = "<repo 밖 경로>"
python scripts/openbinggu_batch_pack_loader.py --pack-dir <pack 디렉터리> --enable-write
```

- write는 기본 OFF — `--enable-write` 없으면 거부됩니다.
- apply 직전 PII/secret 재스캔에서 검출되면 거부됩니다(kind만 출력).

## 5. 승격 전 preview (read-only)

pack을 운영형 그래프로 승격하기 전에 무슨 일이 일어날지 **미리 보기만** 합니다(write 0). 이전의 독립 CLI 스크립트(`openbinggu_promotion_preview.py`)는 아카이브됐고, 지금은 **advanced 프로파일의 MCP 도구**로 대체됐습니다:

- **`publish_guard_dryrun`** — pack 공개/승격 전 스코프 분류 + 게이트 판정을 dry-run으로 확인(write 0).
- **`pack_validate`** — pack 계약(구조·근거·PII 스캔)을 검증.

MCP 클라이언트(Claude·Codex)에서 **advanced 프로파일**로 등록한 뒤(등록법: [INSTALL.md](../INSTALL.md) `--profile advanced`) 위 두 도구를 호출하면 됩니다. 승격 **실행기**는 이 배포에 포함되지 않습니다(preview는 read-only).

✅ 기대: 변환 계획·id 충돌·backup/rollback 계획·write 예정 row 수 등이 출력되고 `verdict: GO`. 실제 승격 write는 로컬 승인 게이트 몫입니다.

## 6. 후보 관리 UX 따라하기 (temp-only)

저장된 후보(candidate)를 **보기 → 기각 → 수정 → 수용 → 철회 → 피드백 resolve**로 관리하는 전체 사이클을 temp DB에서 안전하게 체험할 수 있습니다(내 데이터 0, 실행 후 원복):

```bash
python scripts/openbinggu_v1_candidate_cycle_real_once.py --dry-run-temp   # 17/17 기대
```

✅ 기대: `RESULT: 17/17 PASS` + `GATE: GO` + 종료코드 `0`.

각 단계의 **confirm 문구 형식** (행 번호 `<n>` + id 칼럼의 `<id8>` 함께 — 인덱스 단독 금지):

| 작업 | confirm 문구 예시 | 효과 |
|---|---|---|
| 목록 보기 | (read-only — confirm 불필요) | 행 번호·id8·상태·kind 표시, DB 무변 |
| 기각 | `DEPRECATE 3 a1b2c3d4` | 보존형 제외(삭제 아님) — active 뷰에서만 빠짐 |
| 수정 | `REPLACE 2 a1b2c3d4 WITH 수정된 문장` | 전임자 deprecate(+back-link) 후 신규 candidate로 저장 게이트 전부 재통과 (in-place 수정 없음) |
| 수용 | `ACCEPT 5 a1b2c3d4` | append-only 수용 event (후보 row는 byte-identical) |
| 철회 | `UNACCEPT 5 a1b2c3d4` | 보존형 철회 event (삭제 0) |
| 피드백 | resolve 4값: `성공/실패/불확실/판정불가` + 사유 | 기록만 — `실패`여도 자동 강등 0 |

- confirm 문구가 정확히 일치하지 않으면 BLOCK, `actor=auto`도 전부 BLOCK(사람 발화만).
- 모듈별 selftest 기대값: 목록 13/13 · 기각 15/15 · 수정 19/19 · 수용 16/16 · resolve 16/16 (명령은 `../INSTALL.md` 참조).
- 쓰기 루프(preview→선택 저장→피드백) 통합은 `python scripts/openbinggu_v08_real_cycle_once.py --dry-run-temp` (14/14 기대).

### 내 영속 장부 시작

```bash
python binggu.py init
python binggu.py preview "오늘 정리할 문장들"
```

PyPI 설치자는 같은 명령을 `binggu start`, `binggu remember "..."`로 실행하면 됩니다. 자세한 명령은 README의 후보 관리 표 참조.

### 화자 축 — 내 발화와 AI 요약 따로 쌓기 (v1.12.0 · 양방향 페어 v1.14.0)

빙구팩이 "AI 작업일지"가 아니라 **나 자신**을 쌓게 하는 흐름입니다. 내 발화(owner)와 AI 요약(ai)을 각각 독립 노드로 저장하고 수용/반박/수정 엣지로 연결합니다. 페어는 **노드 2 + 엣지 1을 한 번에**(따로 저장하면 연결이 빠짐), `--by`로 **누가 먼저 말하고 누가 반응했는지**(시간 순서·방향)를 정합니다. 인자 순서는 항상 (owner 발화, ai 발화)이고, owner는 **내가 친 자연어 원문 그대로**(요약 금지).

```bash
# 내가 먼저 판단 → AI가 반박 (반응 주체=AI) → --by ai (기본)
python binggu.py pair "이 입찰은 마진이 낮아 보류한다" "데이터가 부족해 보수적 접근이 맞다" \
    --by ai --relation refutes --confirm "PAIR ai_refutes owner:1 ai:1"

# AI가 먼저 권고 → 내가 뒤집음 (반응 주체=나) → --by owner
python binggu.py pair "그래도 이 건은 응찰한다" "데이터가 부족해 보수적 접근이 맞다" \
    --by owner --relation revises --confirm "PAIR owner_revises owner:1 ai:1"

# 순수 직감만 (AI 노드 안 만듦)
python binggu.py pair "다음엔 이 거래처를 우선 검토하자" --confirm "PAIR owner:1"

# 양방향 신뢰도 — 내 직감 적중률 + AI 반박 적중률 (참고 가중치, 맹종 아님)
python binggu.py trust

# 예측 결과 기록 → 적중률 누적
python binggu.py resolve <n> <id8> --outcome 성공

# 무엇을 할지 헷갈리면 — 신규/수정/결과 안내
python binggu.py route "아까 그 판단 틀렸어"
```

- 저장은 여전히 사람 confirm 게이트(`PAIR ...` 정확 일치)·`actor=auto` BLOCK·PII 제외. 자동 저장 0.
- 신뢰도는 표본 N<5면 미산정(편향 차단), 시간감쇠(반감기 30일)로 최근 결과를 더 반영.
- 상세: [화자 축 설계](BINGGUPACK_SPEAKER_AXIS_DESIGN.md).

## 7. 다음 단계

- **여러 AI에 pack 넘기기**: [Multi-agent handoff guide](BINGGUPACK_PHASE3_MULTI_AGENT_HANDOFF_GUIDE.md)
- **내 pack을 공개하기 전**: [실데이터 검증 절차](_archive/BINGGUPACK_REAL_DATA_VALIDATION_PROCEDURE.md) 필수 — `doctor --tree <공개 후보 트리>`가 CLEAN이어야 하고, 검출 1건이라도 있으면 공개가 차단(BLOCK)됩니다.

## 막혔을 때

- `GATE: GO`가 안 나온다 → 종료코드와 마지막 요약(PASS/FAIL·reason_code)만 확인하세요. raw 경로/원문은 출력되지 않는 것이 정상입니다.
- FTS 검색 결과에서 값이 안 보인다 → contentless FTS 설계입니다. [Promotion Preview 설계](_archive/BINGGUPACK_PROMOTION_PREVIEW_DESIGN.md) §contentless FTS 검증법 참조.
