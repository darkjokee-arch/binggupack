# S4-3 — deprecate_g3 4함수 canonicalization 판정 기록 (HOLD)

**owner token:** ✅ OWNER_TOKEN_APPROVED_FOR_S4_3_LOW_RISK_ENTRY (2026-06-26) — 발급됨.
**S4-3 entry baseline:** `71c4730` (S4-2 closure 후)
**판정:** **BINGGUPACK_S4_3_DEPRECATE_G3_CANONICALIZATION_HOLD** — owner token 발급됐으나, 실측 결과
이관은 **actual write core 접촉 + strangler 단방향 위반 + import cycle 위험**(STOP 조건)에 해당.
코드 변경 0, characterization 은 이미 GREEN(s4gap H4·H5·J3·K4 + deprecate 23/23), 본 문서는 docs-only 경계 기록.

---

## 1. 대상

`scripts/openbinggu_deprecate_and_remind_g3.py` 의 write 4함수 (H·I·J·K):
`deprecate_item` · `set_review_due` · `resolve_review` · `classify_harvest_item`.

## 2. 왜 HOLD인가 — 실측 (S4-2 교훈 적용: 의존 체인·write 결합 먼저 검사)

### 2-1. scripts-only 의존 (strangler 단방향 위반)
모듈 import 체인:
```
from openbinggu_staging_write_selftest import StagingDB, OPERATING_PATHS, _hash, _now_iso
from openbinggu_proposal_batch_approval_g2b import open_staging
from binggu_p1_config import challenge_threshold, is_confirm_actor
```
- `StagingDB`/`_hash`/`_now_iso` — scripts (write 인프라 = S4-4 대상)
- `open_staging` — scripts
- `is_confirm_actor` (G4③ 게이트)/`challenge_threshold` — scripts (`binggu_p1_config`)

→ binggupack 정본화 시 **binggupack → scripts 역의존 불가피** = strangler 단방향 위반(S4-2와 동일).

### 2-2. actual write core 접촉 (S4-3의 결정적 STOP)
write 4함수 **전부** StagingDB write 인프라에 직접 결합 + sqlite write 직접 수행:

| 함수 | write 결합 (실측) |
|---|---|
| `deprecate_item` | `db.store_checksum()` · `db.write_lock()` · `con.execute("BEGIN")` · `UPDATE ... SET state='deprecated'` · `INSERT INTO deprecations` · `db.audit_append(...)` |
| `set_review_due` | `db.write_lock()` · `INSERT INTO judgment_reviews` · `db.audit_append(...)` |
| `resolve_review` | `db.write_lock()` · `UPDATE judgment_reviews SET status='resolved'` · `db.audit_append(...)` |
| `classify_harvest_item` | `db.write_lock()` · `INSERT INTO harvest_classifications` · `db.audit_append(...)` |

→ 이 함수들은 **actual sqlite write 본체**(UPDATE/INSERT) + write 인프라(`write_lock`/`audit_append`/`store_checksum` = S4-4 대상)에 강결합.
design doc §4 분류상 "sqlite write·G4③"이지만, **실측상 actual write core 접촉**이며 "actual write core 접근 금지" 절대 금지선에 닿는다.

### 2-3. import cycle / fallback 불가
S4-1 패턴(try import 정본 / except byte-identical 폴백)을 써도, 폴백 본문이 `StagingDB`/`open_staging`/`is_confirm_actor` 를 다시 참조해야 하므로 scripts 의존 해소 불가. cycle 회피 불가.

## 3. 걸린 STOP 조건 (S4-3 HOLD)

1. scripts-only 의존 발견 ✅
2. **actual write core 접촉**(sqlite UPDATE/INSERT + write_lock/audit/checksum 인프라) ✅
3. import cycle 위험(binggupack→scripts 역의존) ✅
4. write 인프라(S4-4)·write core(S4-6) 결합 ✅

## 4. 현재 안전 상태 (이관 없이도 확보됨)

- **characterization GREEN:** s4gap H4(tombstoned→tombstoned_item)·H5(kind_invalid)·J3(resolve_reason_required)·K4(item_id_required) + deprecate selftest 23/23. G4③(is_confirm_actor allowlist) 방어 + 4함수 가드 전수 pin.
- 이동하지 않아도 동작은 이미 고정·검증됨. 이관 이득 < 위험(strangler 위반 + actual write core 접촉).

## 5. 재진입 조건

S4-3 canonicalization 은 다음 선결 후에만 재검토:
- **S4-4(StagingDB write 인프라)가 먼저 정본화**되어야 한다 — deprecate_g3 write 함수는 StagingDB(`write_lock`/`audit_append`/`store_checksum`)에 결합되므로, write 인프라 정본화가 선결.
- 단 S4-4 자체가 actual write core 토대라 고위험 — 별도 owner token + 대량 characterization 필요.
- scripts 의존(`is_confirm_actor`/`open_staging`)도 binggupack 이관 선결.

## 6. 금지선 준수 (본 작업)

- 코드 변경 **0** — docs-only. actual write core(`staging_apply`+`save_selected`+`commit_selected`) 미접촉.
- deprecate_g3 판정 약화 0 · G4_no_auto/G4③·actor/confirm/token 흐름·ledger write 경로 변경 0.
- production write 0 · OpenCrab ingest 0 · PyPI 0 · tag/release 0 · pyproject/version 변경 0.

---

## 7. S4-4 ~ S4-6 경계 (갱신)

| 단계 | 대상 | write core 도달 | 별도 token | 비고 |
|---|---|---|---|---|
| **S4-4** | C·D (`tombstone`+`StagingDB` write_lock/snapshot/audit/verify/store_checksum) | **강의존(write 인프라 본체)** | 필요 + 고위험 | deprecate_g3(S4-3)·staging_apply(S4-6)가 모두 의존하는 토대. store_checksum anchor `210e04611a157877`·integrity_check=ok 보존 필수. **사실상 write core 인접 — HOLD 유력** |
| **S4-5** | A (`c2_check`) | 강결합(G4②·B 게이트) | 필요 | staging_apply(B)와 함께 검토. scripts 의존(staging_write) 선검사 필요 |
| **S4-6** (마지막/**영구 HOLD**) | B `staging_apply` + E `save_selected` + G `commit_selected` | **= actual write core 본체** | 필요 + 영구 HOLD 후보 | 이번 지시로 **절대 구현 안 함** |

**누적 교훈(S4-2·S4-3):** "순수/저위험 함수처럼 보여도" scripts 의존 체인 + write 인프라 결합으로 binggupack byte-identical 이관이 **불가**한 경우가 많다. **S4-1(gate_log)만이 scripts 의존 0인 깨끗한 케이스**였다. S4-4 이상은 전부 StagingDB write 인프라/actual write core에 닿아 HOLD 유력 — 각 단계 실측 선검토 필수.

## 8. 다음 단계 (S4-4 진입 조건)

- **S4-4 진입 전:** StagingDB write 인프라(`write_lock`/`snapshot`/`audit_append`/`verify_chain`/`store_checksum`)의 정본화 가능성 실측 — 단 이는 **actual write core 토대**라 "actual write core 접근 금지"에 닿을 가능성 높음. 닿으면 즉시 S4-6 분류·HOLD.
- **actual write core(S4-6)는 마지막 또는 영구 HOLD.**
- owner token 없이 S4-4 이상 진입 금지.
