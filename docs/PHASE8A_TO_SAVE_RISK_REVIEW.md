# Phase8-A — to_save 안전검토 리포트 (이관 0, risk review only)

**대상:** `scripts/binggu_capture_to_save.py` (164 LOC)
**기준:** main/origin = bc25f23
**결론:** **SPLIT 필요** — 순수 영역(`build_save_commands`)만 이관 가능, 게이트 위임 영역(`commit_selected`)은 HOLD.

---

## 1. to_save 역할 요약
capture preview → 저장 게이트 연결 **어댑터**(다리). 2개 진입점:
- `build_save_commands(preview, ledger=None)`: 사람이 직접 실행할 `binggu.py save … --preview-id … --pick … --confirm "SAVE n"` **명령 문자열만 생성**. 저장 실행 0, write 0. 의존 = `hashlib`(`_preview_id`)뿐.
- `commit_selected(db, text, preview_id, picks, confirm, snap_dir, due=None, actor="human")`: 기존 `save_selected` 게이트에 **위임**(게이트 통과 시 actual ledger write).

## 2. save_gate와의 관계
- `from openbinggu_conversation_candidate_save import save_selected`(line 24) — **save_gate 핵심 직결**.
- `commit_selected`는 게이트를 **만들거나 우회하지 않음**: confirm 문구를 생성하지 않고 인자값 그대로 전달, `save_selected`의 3중 게이트에 위임.
  1. preview_id 게이트: `preview_id == sha256(text)[:8]` 일치해야 진행
  2. actor 게이트: actor ∈ {auto, reader} → `save_selected`가 **G4_no_auto BLOCK**
  3. confirm 게이트: confirm == `"SAVE <picks>"` 정확일치해야 통과 (불일치/누락 → BLOCK)

## 3. storage resolver와의 관계
- `os` import 있으나 **`os.environ`/`BINGGU_HOME`/`BINGGUPACK_LEDGER` 직접 사용 0**(grep 확인).
- ledger·snap_dir은 **인자로 주입**(`db`, `snap_dir`). resolver 직접 미사용. `build_save_commands`의 `--ledger` 는 명령 **문자열에만** 들어감(실행 0).

## 4. interactive_save와의 관계
- 직접 import/호출 **0**. 둘은 `save_selected` 게이트 위에 얹힌 **별개 진입점**(상호 의존 없음).

## 5. actor / confirm / token 흐름
- **confirm/phrase/token**: 전부 **인자로 받아 그대로 전달 — 어댑터 생성 0**(★ 자동저장 구조적 불가의 핵심).
- **actor**: `commit_selected(..., actor="human")` 기본 인자. 단 actor 단독으로는 저장 불가(confirm 누락 시 BLOCK — T7로 검증). actor=auto는 G4_no_auto BLOCK(T8).
  - ⚠️ 주의: `actor="human"` **기본 인자**가 존재 — 이관 시 기본값을 그대로 보존해야 하며(byte-identical), 새 actor 자동생성 로직 추가는 금지. 게이트 실질 방어는 confirm+preview_id이므로 기본값만으론 저장 0.

## 6. actual write 가능 경로
- `commit_selected` → `save_selected` → **staging ledger write**(게이트 전부 통과 시). dry_run 플래그 없음 — confirm/actor/preview_id 3게이트가 방아쇠를 사람에 고정.
- `build_save_commands`는 write 경로 **없음**(문자열만).

## 7. non-TTY 동작
- to_save 자체엔 TTY 체크 없음(라이브러리 함수, `db` 인자 주입형). non-TTY fail-closed는 호출자/`save_selected` 책임. 어댑터는 게이트를 새로 만들지 않음.

## 8. 이관 가능 영역
- **`build_save_commands` + `_preview_id`**: 순수 함수(hashlib만, write 0, save_gate 무관). `binggupack/capture/`로 이관 가능. 호출처 0(leaf)이라 호환 부담 최소.

## 9. 이관 금지 영역
- **`commit_selected`**: `save_selected`(save_gate 핵심, scripts 잔류) 직결 + actual write 경로.
  - binggupack으로 옮기면 (a) binggupack이 `scripts/openbinggu_conversation_candidate_save`를 **역참조**(strangler 단방향 위반) 하거나 (b) save_selected까지 동반 이관해야 하는데 이는 **save_gate 변경 금지 위반**.
  - 따라서 `commit_selected`는 `save_selected`(save_gate 라인)가 먼저 정본화되기 전까지 **HOLD**.
- selftest(T5~T13 commit_selected 게이트 검증)도 `save_selected`/`open_g3`/`OPERATING_PATHS` 의존이라 commit_selected와 함께 HOLD.

## 10. 다음 권고
**SPLIT** (= 부분 이관):
- **migrate 가능**: `build_save_commands`(+`_preview_id`) → `binggupack/capture/to_save.py`(또는 동명 모듈), scripts는 thin wrapper. 단 한 파일을 둘로 쪼개므로 `commit_selected`/selftest는 scripts 원본에 잔류 → 응집도 일시 저하.
- **hold 필요**: `commit_selected` + 게이트 selftest. save_gate(`save_selected`) 라인 이관 계획이 선 뒤에 함께 옮겨야 strangler 단방향·save_gate 불변식 유지.
- **대안(권장)**: to_save 전체를 capture family **최후 보류**로 두고, save_gate 라인 모듈화 phase를 별도 설계한 뒤 그 단계에서 commit_selected+build_save_commands를 함께 정본 이관. capture family는 classifier·buffer·session·cli 4개로 이미 충분히 정리됨.

> 판정: 순수 영역만 떼면 migrate 가능하나 응집도/단방향 trade-off가 있고, 게이트 영역은 save_gate 선이관이 전제. **사장님 결정 필요: (A) build_save_commands만 split 이관 / (B) to_save 전체 HOLD(save_gate phase까지 보류).**
