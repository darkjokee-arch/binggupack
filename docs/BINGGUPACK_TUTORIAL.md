# BingguPack 10분 튜토리얼 — clone부터 promotion preview까지

> 이 문서는 BingguPack을 처음 받은 사용자가 **데이터 없이도** 전체 흐름을 따라해 보는 가이드입니다.
> 모든 단계는 로컬 synthetic/temp 데이터만 사용하며, 운영 저장소를 변경하지 않습니다.
> 이 튜토리얼은 **CLI/local 흐름** 기준입니다 — 채팅 앱에서 `@BingguPack`처럼 호출하는 앱/모바일 경로는 roadmap 단계(planned)입니다.

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

✅ 기대: 마지막에 `summary: 11/11 PASS` + `GATE: GO` + 종료코드 `0`.
하나라도 다르면 사용을 멈추고 이슈를 확인하세요.

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

> **내 pack은 어디서 오나?** pack 생성은 OpenCrab 도구 흐름(`opencrab.sh`)에서 수행하고, BingguPack은 그렇게 만들어진 pack을 **검증·적재·preview**하는 쪽을 담당합니다. 흐름 개요는 [USER_DRIVEN_OPENCRAB_UPLOAD_FLOW](OPENBINGGU_USER_DRIVEN_OPENCRAB_UPLOAD_FLOW.md) 참고. (여기서 OpenCrab apply/finalize/upload를 실행하라는 뜻이 아닙니다 — 그 단계들은 별도 승인 영역이며 이 튜토리얼 범위 밖입니다.)

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

## 6. 다음 단계

- **여러 AI에 pack 넘기기**: [Multi-agent handoff guide](OPENBINGGU_PHASE3_MULTI_AGENT_HANDOFF_GUIDE.md)
- **내 pack을 공개하기 전**: README의 "Validate your real data" 절차 필수 — `doctor --tree <공개 후보 트리>`가 CLEAN이어야 하고, 검출 1건이라도 있으면 공개가 차단(BLOCK)됩니다.

## 막혔을 때

- `GATE: GO`가 안 나온다 → 종료코드와 마지막 요약(PASS/FAIL·reason_code)만 확인하세요. raw 경로/원문은 출력되지 않는 것이 정상입니다.
- FTS 검색 결과에서 값이 안 보인다 → contentless FTS 설계입니다. [Promotion Preview 설계](OPENBINGGU_PROMOTION_PREVIEW_DESIGN.md) §contentless FTS 검증법 참조.
