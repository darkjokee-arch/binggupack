# S4-1 — save_gate write/판정 정본화 (gate_log canonicalization) 완료 기록

**owner token:** ✅ **OWNER_TOKEN_APPROVED_FOR_S4_1_LOW_RISK_ENTRY** (2026-06-26)
**S4-1 entry baseline:** `807dc7d` (코드 = `10df67c`, v1.12.0 speaker axis 반영)
**방식:** byte-identical relocation · **semantic change 0**
**판정:** **BINGGUPACK_S4_1_GATE_LOG_CANONICALIZATION_GO**

---

## 1. 대상 (L·M·N·O 4함수 + 의존 helper)

`scripts/binggu_save_gate.py` → 정본 `binggupack/safety/gate_log.py` 로 이동:

| 심볼 | 종류 | 비고 |
|---|---|---|
| `gate_record` (L) | gate-log append write | 사람 SAVE 발화 hash 기록 |
| `gate_human_for` (M) | 판정(read) | 저장 문장이 사람 발화로 기록됐나 대조 |
| `write_last_preview` (N) | preview 영속(atomic) | hash-only·원문 미저장 |
| `gate_record_from_prompt` (O) | hook 진입점 write | 'SAVE n' → idx hash 기록 |
| `_resolve_home`/`gate_home`/`gate_path`/`last_preview_path`/`_gate_path` | home resolver | 4함수 경로 의존성(함께 이동) |
| `_load` | gate-log read helper | gate_human_for 의존 |
| `GATE_WINDOW_SEC` | 상수 | 신선도 창(env, 기본 3600) |

> **미이관(잔류):** `has_trigger_token`/`TRIGGER_TOKENS` (S3-CLOSURE §2-1 미이관 확정·hook 빠른차단 self-use),
> `__getattr__`/`_LAZY_ATTRS` lazy 속성(모듈 레벨 PEP 562 — scripts 모듈에 유지), `_selftest`.

## 2. 정본 위치 / wrapper

- **정본:** `binggupack/safety/gate_log.py` — self-contained. home resolver 는 `binggupack.workspace.platform`(정본·S1) 경유, 파싱 helper 는 `binggupack.safety.gate_text`(정본·S3) 경유, 각 import 실패 시 byte-identical 폴백.
- **wrapper:** `scripts/binggu_save_gate.py` — `try: from binggupack.safety.gate_log import (...)` / `except: 동일 정의 폴백`(S1~S3 확립 패턴). 외부 호출자는 기존 이름(`from binggu_save_gate import gate_record` / `import binggu_save_gate as sgate; sgate.gate_human_for`) 그대로 사용. lazy 속성(`GATE_PATH`/`LAST_PREVIEW_PATH`/`_HOME`)·`has_trigger_token` 호환 유지.

## 3. semantic change 0 보증

- 함수 본체 로직·문자열·반환값·예외 처리·skip 조건·파일 write 방식(append/atomic replace)·함수명/시그니처·`GATE_WINDOW_SEC` 값·경로 계산 의미 **전부 무변**.
- 정본 import 경로 실측: `gate_record/gate_human_for/write_last_preview/gate_record_from_prompt.__module__ == binggupack.safety.gate_log`. `has_trigger_token.__module__ == binggu_save_gate`(잔류).
- 폴백 정의는 정본과 동일(독립 실행 byte-identical). `py_compile` OK.

## 4. 금지선 준수 (owner token 범위 엄수)

- **actual write core(`staging_apply`+`save_selected`+`commit_selected`) 미접촉** — S4-6 마지막/영구 HOLD.
- `deprecate_g3`·`to_save`·`build_save_commands`·`has_trigger_token`(미이관) 변경 0.
- **G4_no_auto 3중 방어·actor/confirm/token 흐름·dry_run/actual save 분기·ledger write 경로 변경 0** — gate_log 는 gate-log append + 사람 발화 판정 read 만(운영 ledger 와 별개 파일).
- production write 0 · OpenCrab ingest 0 · PyPI 0 · tag/release 0 · pyproject/version 변경 0.

## 5. 회귀 (이동 후, 807dc7d 기준)

| selftest | 결과 |
|---|---|
| staging | 16/16 |
| candidate | 19/19 |
| deprecate | 23/23 |
| capture | GO |
| **save_gate** | **28/28** (정본 경로 동작) |
| **S4 GAP** | **41/41** (operating_store_unchanged=True · production_code_touched=0) |
| version SSOT | 3/3 v1.12.0 |
| package import | 1.12.0 |

## 6. 다음 단계

- **S4-2 (F `_maybe_promote_actor_by_gate`)** 는 **별도 owner approval/token 후** 진행 — fail-closed 4분기 전수 pin 전제(이미 s4gap F1~F4 커버).
- **actual write core(S4-6: `staging_apply`+`save_selected`+`commit_selected`)는 마지막 또는 영구 HOLD.**
- owner token 없이 S4-2 이상 진입 금지.
