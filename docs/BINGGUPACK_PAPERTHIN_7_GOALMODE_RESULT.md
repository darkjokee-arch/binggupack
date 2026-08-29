# BingguPack × Paperthin 7개 선택 패턴 Goal Mode 결과

기준: BingguPack `origin/main` `9e5a66b`, Paperthin은 설계 참고만 사용했다. Paperthin runtime, hook, updater, installer, 전역 설정은 도입하지 않았다.

## A. 최종 architecture

```text
agent/request
  ├─ reconstruct_intent                     readchk, pure
  ├─ canonical Studio recall                existing read model
  ├─ select_load_bearing_objection          hate, evidence-bound result
  ├─ fact_check_candidate                   factchk, supplied evidence only
  ├─ propose_sip_candidates                 sip, pure canonical preview
  ├─ existing human SAVE path               unchanged authority
  ├─ existing recall trace/outcome writer   unchanged signal-only telemetry
  └─ select_next_best_action                nba, recall-aware; outcome is display-only

new session
  └─ binggu catchup
       ├─ live Git state (fsmonitor/untracked cache disabled)
       ├─ Studio lexical recall/superseded snapshot
       ├─ canonical read-only outcome snapshot
       └─ bounded re-entry briefing

eval only
  └─ binggupack.eval.paperthin               mandela + bounded A/B/C evaluator
```

공개 runtime surface는 읽기 전용 `binggu catchup` 하나다. 검토 중 만들었던 JSON `workloop` command와 병렬 data plane은 제거했다. 일반 작업 연결은 새 orchestrator가 아니라 기존 recall/trace/outcome/approval 경로와 순수 함수의 조합이다.

주요 코드:

- `binggupack/cognitive/catchup.py`: live state와 canonical read snapshot 합성, context hard cap, read-only 방어
- `binggupack/cognitive/patterns.py`: readchk, hate, sip, nba, factchk
- `binggupack/studio/read_model.py`: superseded decision read snapshot
- `binggupack/pack/outcome_attribution.py`: side-effect-free outcome list
- `binggupack/eval/paperthin.py`: eval-only Mandela audit
- `tests/test_cognitive_integration.py`: 실제 action/evidence/trace/outcome/re-entry 폐루프 fixture

## B. 7개 판정

| Pattern | Result | Implementation | Evidence |
| --- | --- | --- | --- |
| catchup | GO | `binggu catchup`; live state 우선, bounded canonical recall/outcome | clean/dirty/WAL/fsmonitor/context/overturn/write-zero tests |
| readchk | MODIFY | intent·constraint·deliverable·ambiguity·recall query를 순수 구조화 | 명확/장문/충돌/available facts/material ambiguity tests |
| hate | MODIFY | 고위험 변경에서 반론 1개와 최저비용 test 1개; test digest가 없으면 `TEST_REQUIRED` | blocker/pass/fail/unbound/low-risk tests |
| sip | MODIFY | semantic/cache가 꺼진 canonical preview로 ephemeral candidate 제안 | duplicate/PII/pure-preview/write-zero/authority tests |
| nba | MODIFY | recall만 counterfactual ranking에 사용; outcome은 기존 계약대로 `signal_only` | recall 유무로 선택 변경, outcome-only 비변경 fixture |
| factchk | MODIFY | 외부 사실에만 적용; URI·source digest·claim digest·시각을 모두 검증 | verified/contradicted/stale/future/malformed/unavailable tests |
| mandela | MODIFY | runtime에서 분리한 eval-only manifest+observation audit | leakage/coupling/contamination/baseline/duplicate/metric tests |

## C. Safety proof

- 자동 SAVE/승인/commit 호출을 cognitive layer에 추가하지 않았다.
- 모든 SIP 후보는 `promotion_allowed=false`; factual 후보는 현재 SAVE가 구조화 evidence를 exact-bind하지 못하므로 `canonical_gate_eligible=false`, `EPHEMERAL_ONLY`로 차단한다.
- `G4_no_auto`, human approval, exact binding, immutable bundle, rollback, 기존 provenance/evidence 코드는 변경하지 않았다.
- catchup은 `GIT_OPTIONAL_LOCKS=0`, repo-controlled fsmonitor 비활성화, SQLite WAL/no-SHM 상태 fail-closed를 적용한다.
- catchup/SIP 적대 fixture에서 repository, ledger, sidecar, semantic cache write가 0임을 파일 tree hash로 확인했다.
- 사람이 overturn한 outcome은 catchup failure 또는 NBA 근거로 되살리지 않는다.
- 외부 network/account/GitHub/PyPI/release/deploy mutation은 0이다.

## D. 폐루프 검증

격리 fixture가 다음을 직접 실행한다.

```text
readchk query
→ canonical Studio recall
→ recall로 NBA 선택 변경
→ subprocess action 실행
→ 실제 artifact SHA-256 생성
→ canonical recall trace
→ existing record_run_outcome
→ canonical read-only outcome reload
→ catchup이 다음 세션에 failure/evidence 복원
→ 사람이 해석한 current blocker로 다음 NBA 변경
```

outcome은 기존 헌법상 랭킹 입력이 아니다. 따라서 마지막 선택 변화는 raw outcome 자동 점수화가 아니라 catchup에 노출된 signal을 사람/호출자가 현재 blocker로 해석한 뒤 발생한다.

## E. Behavioral eval

현재 판정은 **INSUFFICIENT EVIDENCE**다.

- A/B/C reference fixture는 3 scenario, 9 observation을 만든다.
- 같은 코드가 fixture/관찰을 생성하므로 Mandela가 `SCORER_OBSERVATION_COUPLING`으로 BLOCK한다.
- manifest-only 평가, 중복/누락 observation, fixture/scorer binding mismatch, metric regression을 차단한다.
- 따라서 이번 결과는 mechanics와 회귀 안전성을 증명하지만 일반 성능 향상이나 인과 효과를 주장하지 않는다.

## F. Verification

- cognitive/closed-loop targeted: `20 passed`
- full pytest: `503 passed, 4 skipped`
- `binggu.py --selftest`: `71/71 PASS`
- save exact binding: `18/18 GO`
- bundle atomicity: `22/22 PASS`
- capture preview: `21/21 PASS`, fs/db write 0
- outcome attribution selftest: `GO`
- publish regression: `56/56 PASS`
- lifecycle E2E: `12/12 GO`
- ruff: new code/tests 0 issue; repository F-gate PASS
- mypy: cognitive/eval 7 modules PASS
- `git diff --check`: PASS
- final adversarial reviews: Architecture 0/0, Safety 0/0, Behavioral/Eval 0/0 (Critical/High)
- final 4-CLI debate: option A consensus, normalized majority confidence `0.87`, `ship_recommendation=GO`

`ci_local_preflight.py`는 version, ruff, vendor sync, platform, selftest, doctor, setup-cloud, run-all, E2E까지 PASS한 뒤 출력 없는 pytest 구간에서 운영 규칙에 따라 중단했다. 동일 checkout의 pytest는 진행 출력 모드로 별도 완주해 위 `503/4` 결과를 얻었다.

## G. Remaining limitations

1. 외부 factual candidate의 verification bundle을 기존 text-only SAVE exact binding에 보존할 schema가 없다. 안전하게 차단했으며 자동 우회는 없다.
2. outcome은 `signal_only`여서 NBA를 자동 변경하지 않는다. 사람/호출자의 명시적 current-state 해석이 필요하다.
3. 기존 outcome row에는 action id exact binding이 없다. fixture는 실제 action artifact digest를 연결하지만 schema 수준 action attribution은 후속 과제다.
4. Mandela의 독립 scorer/observer 신원은 암호학적으로 증명하지 않는다. 그래서 자체 reference 결과는 PASS가 아니라 `INSUFFICIENT EVIDENCE`다.
5. WAL residue에서 SHM이 없으면 catchup은 memory를 생략하고 fail-closed한다. repository live state는 계속 제공한다.
6. MCP, global hook, Paperthin runtime을 추가하지 않았다. 선택 패턴은 agent-side 순수 adapter와 catchup CLI로만 제공한다.
7. 운영 ledger 내용은 읽지 않았다. 작업 중 메타데이터가 `2056192 / 2026-08-29T03:36:56Z / 31A0FA04`에서 `2056192 / 2026-08-29T04:10:13Z / B3CF3F01`로 바뀐 동시 외부 mutation을 관찰했다. 모든 본 작업 writer/test는 격리 경로였고 인과 귀속은 하지 않는다.

## H. Commits

- `602b776` docs: review selective Paperthin integration
- `a36d386` feat(cognitive): add seven-pattern workloop
- `f6cb5c9` test(cognitive): prove closed-loop behavior
- `ba9e581` fix(cognitive): preserve canonical safety contracts
- `docs(cognitive): record seven-pattern goal result` (this report commit)

최종 상태에서는 초기 `workloop` 공개 surface가 `ba9e581`에서 제거되었고, 기존 canonical core를 재사용하는 thin adapter만 남는다.

## I. Next Best Action

이 로컬 브랜치를 PR로 열어 독립 CI runner에서 동일 507-test matrix를 재실행하고, 자체 생성이 아닌 외부 고정 behavioral fixture/scorer를 별도 후속 PR로 설계한다.
