# BingguPack v1.11.0 — Release Closure

> v1.11.0 feature implementation release를 닫는 closure 기록.
> 상태: `BINGGUPACK_V1_11_0_STABLE_RELEASED`.

- Repo: `darkjokee-arch/binggupack`
- Release: <https://github.com/darkjokee-arch/binggupack/releases/tag/v1.11.0> (Latest · prerelease=false · draft=false)
- Stable line: `main = v1.11.0`

## 1. Release summary

v1.11.0은 v1.10.0 installable MCP stable baseline 위에 올린 **feature implementation release**입니다.

- **Phase 1 package modularization** — 새 `binggupack/` 패키지(cli/classifier/mcp/pack/safety/workspace). smoke 로직을 `binggupack/pack/smoke.py`로 이관.
- **backward-compatible** — `scripts/smoke_test.py`는 thin wrapper로 동작 유지(byte-identical), `scripts/install_claude_mcp.py` 무변경. MCP 8도구 그대로 노출.
- **interactive save prototype** — `binggupack/cli/interactive_save.py`. confirm phrase 유지, non-TTY fail-closed, ledger write 0.
- **pack/workflow examples** — 4 synthetic 시나리오(travel/patent_intel/restaurant_brand/generic_handoff), `ingest_performed=false`.
- **packaging/build readiness** — `pyproject.toml` build-system + version `1.11.0` 확정. 격리 venv sdist/wheel build 검증.

## 2. Final commit map

| 단계 | commit |
|---|---|
| v1.11.0 candidate (docs/roadmap) | `acbb88a` |
| feature implementation | `2555123` |
| version finalization (1.11.0.dev0 → 1.11.0) | `e8c766f` |
| main merge (--no-ff) | `e403ee5` |

- **annotated tag `v1.11.0` → `e403ee5`** (post-merge main HEAD).
- **v1.10.0 tag는 `d15e6d6`에 고정** — v1.11.0 작업이 이전 release artifact를 이동시키지 않았습니다.

## 3. Verification evidence

| 항목 | 결과 |
|---|---|
| pre-merge regression | PASS |
| post-merge regression | PASS |
| external clean-clone (main `e403ee5`) | PASS |
| `scripts/smoke_test.py` | 10/10 PASS |
| package import version | `1.11.0` |
| install `--help` | OK |
| interactive selftest / non-TTY fail-closed | 8/8 PASS / exit 2 |
| examples JSON | 4/4 parse, `ingest_performed=false` |
| isolated venv build | sdist + wheel 생성 PASS |
| wheel install/import/entrypoint/selftest | PASS |
| MCP 8 tools exposure | 유지 |
| `save_candidate(dry_run=false)` | `G4_no_auto BLOCK` |

## 4. Safety invariants

- local-first · preview-first · candidate-first · evidence-backed
- no AI autosave · human-confirmed SAVE gate 유지
- `save_candidate(dry_run=false)` → `G4_no_auto BLOCK`
- production write 0 · OpenCrab ingest 0 · G4 bypass 0 · operating ledger durable write 0
- PyPI publish 0
- v1.10.0 tag `d15e6d6` 불변

## 5. What was not done

- PyPI publish (미수행)
- OpenCrab ingest / production write (0)
- 116-script 대량 이동 (future work)
- interactive save gate 실 게이트 함수 직접 위임 (future)

## 6. Known remaining work

1. Lane B — 116 스크립트 one-off/dev/fixture 대량 이동(분류표 + Phase 회귀).
2. Lane C — interactive save gate 실 게이트 함수 직접 위임(subprocess).
3. Lane E — PyPI publish(별도 owner 승인).

## 7. Next options (owner decision 대기)

- PyPI distribution (실 publish, 별도 승인)
- Lane B 모듈화 완료
- examples → 실 OpenCrab handoff dry-run 확장
- v1.12.0 roadmap

---

_Closure: v1.11.0 stable 사이클 종료. v1.10.0(`d15e6d6`) baseline 불변, PyPI publish 0._
