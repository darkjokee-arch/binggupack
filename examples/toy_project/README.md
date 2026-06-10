# toy_project — BingguPack 최소 예제 (synthetic)

> 이 디렉터리는 **합성(synthetic) 예제**입니다. 실제 개인 데이터·실경로·비밀값·원본 source pointer는 들어있지 않습니다.
> BingguPack을 처음 받은 사용자가 "받기 → 검사 → pack 만들기 → 검증 → 읽기 → (공개 판정)" 흐름을 바로 따라 할 수 있게 만든 toy 입니다.

---

## 0. 무엇인가

`input/toy_notes.md`(합성 작업 메모)를 입력으로 candidate pack을 만들고 검증하는 최소 흐름을 보여줍니다. 모든 데이터는 가짜이고, 도구는 로컬에서 read/dry-run으로만 동작합니다(운영 저장소·외부 전송 없음).

## 1. 폴더 구성

```
examples/toy_project/
├── README.md                     # 이 파일
├── input/
│   └── toy_notes.md              # 합성 입력(작업 메모)
└── expected/
    └── toy_pack_summary.json     # pack 빌드 시 기대 요약(예시)
```

## 2. 실행 (CLI)

```bash
# 0) 한 번에 공개 전 자가검사
python scripts/openbinggu_doctor.py --selftest          # 12/12 GATE=GO 기대

# 1) 이 toy 트리를 공개 후보로 스캔(secret/PII/경로) — CLEAN 이어야 함
python scripts/openbinggu_doctor.py --tree examples/toy_project

# 2) (개념) input 으로 candidate pack 빌드 → 검증 → 읽기
python scripts/watcher_pack_builder_m0.py --selftest    # 빌드 + source pointer 판정
python scripts/openbinggu_pack_validate.py --selftest   # 검증
python scripts/openbinggu_pack_consumer_smoke.py --selftest  # 읽기 smoke
```

> 각 명령은 끝에 `GATE: GO` + 종료코드 `0` 이면 통과입니다.

## 3. 기대 결과

`expected/toy_pack_summary.json` 참고. 핵심:
- 모든 노드/엣지/근거는 **candidate**, `promotion_allowed=false`(자동 승격 없음).
- **source pointer 미포함이 디폴트**: 공개 pack에는 원본 위치(파일 절대경로 등)를 넣지 않습니다. 필요 시에만 사용자가 명시 승인 후 별도 정책으로 추가합니다.
- 출력은 카운트·라벨 중심이며 원문/비밀값/실경로는 표시하지 않습니다.

## 4. 언제 공개가 막히나 (BLOCK)

다음이 트리에 섞이면 공개·업로드가 **자동 차단(BLOCK)** 됩니다(요약만 표시, 원본 값은 출력하지 않음):
- 판단 불가(unknown)이거나 비공개 절대경로 형태의 source pointer
- 비밀키·접근 토큰·자격증명 류, 또는 개인정보(연락처·식별번호 등)
- 사용자 홈 절대경로·사내 주소·내부 IP 같은 비공개 경로

> 이 toy 는 위 항목을 **일부러 포함하지 않으므로** `--tree` 스캔이 CLEAN 이어야 정상입니다.

## 5. OpenCrab 업로드는?

OpenCrab은 **각 사용자가 가입해서 자기 pack을 자기 의지로 올리는 곳**입니다(우리가 자동으로 쌓는 중앙 저장소가 아님). 업로드도 GitHub 공개와 **동일한 차단 기준 + 사용자 수동 승인**을 거칩니다.

> ⚠️ 현재는 **docs 기준 흐름만 정의**돼 있고, **실제 OpenCrab 업로드 API/연결은 제공되지 않습니다(미구현)**. 자세한 흐름은 상위 `docs/OPENBINGGU_USER_DRIVEN_OPENCRAB_UPLOAD_FLOW.md` 참고.
