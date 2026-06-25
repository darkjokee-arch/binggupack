# save-gate S3-A — pure helper 분리 조사 (survey only, 이관 0)

**기준:** main/origin = 74c9e76
**목적:** save_gate 라인 pure helper 전수 조사 + 첫 이관 후보 1개 확정. **코드 이관 0.**
**최종 판정:** **S3B_READY** — 첫 후보 `parse_save_indices`(+`SAVE_TRIGGER_RE`) 확정.

---

## 1. pure helper 후보 목록 (save_gate / to_save 내)
| helper | 위치 | 본문 | 의존 |
|---|---|---|---|
| `_norm(s)` | save_gate L82 | `re.sub` 공백정규화 | 없음 |
| `sent_hash(s)` | save_gate L86 | sha256(_norm(s))[:16] | `_norm` |
| `has_trigger_token(prompt)` | save_gate L100 | TRIGGER_TOKENS substring | 상수 `TRIGGER_TOKENS` |
| `parse_save_indices(prompt)` | save_gate L106 | `SAVE_TRIGGER_RE.fullmatch` → 인덱스 | 상수 `SAVE_TRIGGER_RE` |
| `build_save_commands(preview)` | to_save | 명령 문자열 생성 | `_preview_id`(hashlib) |

## 2. 후보별 위험도 표
| helper | write 0 | ledger 0 | actor/confirm/token 흐름 0 | save_selected/staging 호출 0 | resolver 의존 | 실제저장 인접도 | 외부 호출처 |
|---|---|---|---|---|---|---|---|
| `parse_save_indices` | ✅ | ✅ | ✅(순수 파싱) | ✅ | 무 | 낮음(입력 파싱) | **0** |
| `sent_hash`(+`_norm`) | ✅ | ✅ | ✅ | ✅ | 무 | 낮음(범용 hash) | autopush 1곳 |
| `has_trigger_token` | ✅ | ✅ | ✅ | ✅ | 무 | 낮음(트리거 감지) | 0 |
| `_norm` | ✅ | ✅ | ✅ | ✅ | 무 | 없음(범용) | save_gate 내부 |
| `build_save_commands` | ✅ | ✅ | confirm **문자열 생성**(명령 안내·실행 0) | ✅(위임 안 함) | 무 | **높음(to_save 인접)** | (phase8a HOLD) |

> 게이트-critical(이번 제외 확정): `gate_record`/`gate_record_from_prompt`/`write_last_preview`(gate log append write), `gate_human_for`(actor 승격 판정), `gate_home`/`gate_path`/`last_preview_path`/`__getattr__`(resolver 경유·stage0 split-brain 핵심).

## 3. 첫 이관 추천 후보 — `parse_save_indices` (+ `SAVE_TRIGGER_RE`)
근거:
- **가장 독립적**: 함수 의존 0(상수 `SAVE_TRIGGER_RE` 1개만), 외부 호출처 **0**(save_gate 내부 self-use만) → 이관해도 호출처 깨질 위험 0.
- 순수 텍스트 파싱(`"SAVE 1,3"` → `[1,3]`): write/ledger/actor/confirm/save_selected/staging/resolver 전부 무관.
- 결정론적(입력→고정 출력) → characterization 용이.

## 4. 제외 후보와 이유
- **`build_save_commands`**: to_save 인접(phase8a HOLD 영역)·confirm 문구 문자열 생성 포함. 이번 첫 이관 제외(지시 명시).
- **`sent_hash`(+`_norm`)**: 2순위. 외부 호출처 1곳(`binggu_publish_autopush.py:128` `SGATE.sent_hash`) 있어 정본화 이득은 있으나 `_norm` 동반 묶음 → 첫 이관은 더 고립된 것 우선.
- **gate_record / gate_human_for / write_last_preview**: gate log **append write** = gate-critical. S4 영역.
- **gate_home / gate_path / last_preview_path / `__getattr__`**: resolver(binggu_platform) 경유 + stage0 split-brain 핵심. 분리 시 split-brain 위험. S4 영역.

## 5. 필요한 characterization (S3-B 이관 전)
- `parse_save_indices("SAVE 1")` → `[1]` / `"SAVE 1,3"` → `[1,3]` / `"저장 2"` → `[2]` / `"세이브 1,2"` → `[1,2]`(한글 트리거)
- 부정/부분문자열/인용문 무시: `"이거 SAVE 안해"`·`"SAVE 라고 썼다"` → `None`(fullmatch 아님)
- 공백 변형: `"저장1"`·`"저장 1"`·`"SAVE  1 , 3"` 동작 고정
- 빈/None: `""`/`None` → `None`
- 대소문자: `"save 1"`/`"Save 1"` → `[1]`
- deterministic·write 0
- wrapper(scripts) ↔ package(binggupack) 동일 함수 identity

## 6. S3-B 실행안
1. branch `feat/save-gate-line-s3b-parse-save-indices`.
2. characterization selftest 먼저 추가(위 5의 케이스) → 현행 save_gate에서 PASS 확인.
3. `binggupack/safety/gate_text.py` 신설: `parse_save_indices` + `SAVE_TRIGGER_RE` 이관(순수). `binggupack/safety/__init__.py` re-export.
4. **save_gate import 경로 변경(주의)**: `scripts/binggu_save_gate.py`가 자기 정의 대신 `from binggupack.safety.gate_text import parse_save_indices, SAVE_TRIGGER_RE` import. **이는 정책/게이트 로직 변경이 아니라 helper import 경로 1줄 이동**(기능 byte-identical) — save_gate의 gate_record/gate_human_for/G4 로직은 미접촉. (save_gate가 lazy import되는 hook 환경에서 sys.path 정합 확인 필요.)
5. 양형태(package/wrapper-경유) + save_gate 28/28(split-brain T14~T18 포함) 회귀.
6. ledger integrity_check + 좁은 구간 mtime + 외부 SAVE 분리(S1/S2 패턴).

> ⚠️ S3-B는 save_gate **파일을 건드린다**(helper import 라인). 단 게이트 정책/actor/confirm/G4/write 로직은 0 변경. "save_gate 변경 금지"가 정책 불변을 뜻하면 import 경로 이동은 허용 범위이나, 파일 자체 무수정이 조건이면 S3-B 진입 전 사장님 확인 필요(또는 helper를 binggupack에 두고 save_gate는 자기 정의 유지=중복·정본화 미달 대안).

## 7. 최종 판정
**S3B_READY** — 첫 후보 `parse_save_indices`(+`SAVE_TRIGGER_RE`) 확정. 단 S3-B는 save_gate import 라인 1줄 변경을 수반하므로(정책/게이트 로직 0 변경) 진입 전 "save_gate 파일 무수정" 해석 여부를 사장님이 확정해야 한다. 무수정이 절대 조건이면 첫 이관 자체가 SPLIT_REQUIRED(helper만 옮기고 save_gate는 중복 유지)거나 S4까지 HOLD.
