# BingguPack 10분 튜토리얼 — clone부터 후보 관리(candidate UX)까지

> 이 문서는 BingguPack을 처음 받은 사용자가 **데이터 없이도** 전체 흐름을 따라해 보는 가이드입니다.
> 모든 단계는 로컬 synthetic/temp 데이터만 사용하며, 운영 저장소를 변경하지 않습니다.
> 이 튜토리얼은 **CLI/local 흐름** 기준입니다 — 채팅 앱 경로(hosted custom connector)는 **read-only 6도구**가 Claude·ChatGPT에서 동작 확인되었습니다(배포 방법: `../hosted/workers/README.md`). 채팅에서의 **저장(save-intent)은 v1.2.0부터 동작 검증**되었습니다 — 폰 미리보기→`SAVE n`→PC 러너 pull→로컬 장부 저장 (`../README.md` 상태표·`BINGGUPACK_HOSTED_SAVE_INTENT_DESIGN.md` 참조).

## 0. 준비물

- Python 3.10+ (표준 라이브러리 위주 — 별도 패키지 설치 불필요)
- 외부 네트워크·DB·Neo4j 전부 불필요

## 1. 받기

```bash
git clone https://github.com/darkjokee-arch/binggupack.git
cd binggupack
```

## 2. 동작 확인 (doctor)

```bash
python scripts/openbinggu_doctor.py --selftest
```

✅ 기대: 마지막에 `summary: 15/15 PASS` + `GATE: GO` + 종료코드 `0`.
(버전에 따라 검사 개수는 늘어날 수 있습니다 — `GATE: GO` + 종료코드 `0`이면 정상입니다. `GATE: GO`가 아니거나 종료코드가 0이 아닐 때만 사용을 멈추고 이슈를 확인하세요.)

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

> **내 pack은 어디서 오나?** pack 생성은 이 repo의 빌더 `scripts/watcher_pack_builder_m0.py <입력 디렉터리>`(3단계에서 selftest로 본 그 스크립트)로 로컬에서 수행하고, BingguPack은 그렇게 만들어진 pack을 **검증·적재·preview**하는 쪽을 담당합니다. 흐름 개요는 [USER_DRIVEN_OPENCRAB_UPLOAD_FLOW](OPENBINGGU_USER_DRIVEN_OPENCRAB_UPLOAD_FLOW.md) 참고. (여기서 OpenCrab apply/finalize/upload를 실행하라는 뜻이 아닙니다 — 그 단계들은 별도 승인 영역이며 이 튜토리얼 범위 밖입니다.)

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

## 5. 승격 전 preview (v0.4.0-rc1, read-only)

pack을 운영형 그래프로 승격하면 무슨 일이 일어날지 **미리 보기만** 합니다(write 0):

```bash
python scripts/openbinggu_promotion_preview.py --selftest    # 12/12 기대
python scripts/openbinggu_promotion_preview.py --pack-dir <pack 디렉터리> --domain D10
```

✅ 기대: D1~D4 변환 계획·id 충돌·FTS insert 계획·backup/rollback 계획·write 예정 row 수가 출력되고, 마지막에 `PREVIEW VERDICT: GO`. 승격 실행기는 이 RC에 포함되지 않습니다.

자기 운영형 DB와 대조하고 싶으면 (read-only로만 엽니다):

macOS/Linux (bash):
```bash
OPENBINGGU_OPERATING_DB=<자기 DB 경로> python scripts/openbinggu_promotion_preview.py --pack-dir <dir> --domain D10
```

Windows (PowerShell):
```powershell
$env:OPENBINGGU_OPERATING_DB = "<자기 DB 경로>"
python scripts/openbinggu_promotion_preview.py --pack-dir <dir> --domain D10
```

## 6. 후보 관리 UX 따라하기 (v0.9.0-rc1, temp-only)

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
- 모듈별 selftest 기대값: 목록 13/13 · 기각 15/15 · 수정 16/16 · 수용 16/16 · resolve 16/16 (명령은 `../INSTALL.md` 참조).
- 쓰기 루프(preview→선택 저장→피드백) 통합은 `python scripts/openbinggu_v08_real_cycle_once.py --dry-run-temp` (14/14 기대).

### 내 영속 장부 시작 (v1.0.0)

```bash
python binggu.py init
python binggu.py preview "오늘 정리할 문장들"
```

자세한 명령은 README의 후보 관리 표 참조.

## 7. 다음 단계

- **여러 AI에 pack 넘기기**: [Multi-agent handoff guide](OPENBINGGU_PHASE3_MULTI_AGENT_HANDOFF_GUIDE.md)
- **내 pack을 공개하기 전**: [실데이터 검증 절차](OPENBINGGU_REAL_DATA_VALIDATION_PROCEDURE.md) 필수 — `doctor --tree <공개 후보 트리>`가 CLEAN이어야 하고, 검출 1건이라도 있으면 공개가 차단(BLOCK)됩니다.

## 막혔을 때

- `GATE: GO`가 안 나온다 → 종료코드와 마지막 요약(PASS/FAIL·reason_code)만 확인하세요. raw 경로/원문은 출력되지 않는 것이 정상입니다.
- FTS 검색 결과에서 값이 안 보인다 → contentless FTS 설계입니다. [Promotion Preview 설계](OPENBINGGU_PROMOTION_PREVIEW_DESIGN.md) §contentless FTS 검증법 참조.
