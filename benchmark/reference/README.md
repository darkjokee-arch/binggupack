# Memory PR — Reference E2E (로컬 참조구현)

**Memory PR Spec 의 실행 가능한 로컬 참조구현.** [docs/memory-pr](../../docs/memory-pr) 가 정적 계약
(필드·산식·불변식)을 정의한다면, 이 패키지는 그 계약을 실제 로컬 프로세스로 돌려 보이는
**cross-adapter E2E** — "모델 A 저장 → 모델 B recall/explain" — 를 제공한다.

- **모델 A(writer)** = `BingguPackAdapter` — preview→save(사람 승인 · CLAUDECODE unset)로 활성 기억을 만든다.
- **모델 B(reader)** = `ReaderOnlyAdapter` — 같은 공유 격리 홈을 읽기 바인딩해 **새 프로세스**에서 recall/explain 만 한다.
- **외부 미접촉** — 네트워크 egress 0. `requests`/`urllib`/`http.client`/`socket`/`httpx` import 없음.
  Hosted 최종저장은 로컬 `commit_bundle` 수렴 경로로만 단언하고, 실 worker HMAC/DO 는
  이 참조구현에서 실행하지 않는다(UNSUPPORTED/illustrative 정직 표기).

## 실행

```bash
# 오케스트레이터 직접 실행 (사람 판독 요약, GO=0)
python -m benchmark.reference.e2e_cross_adapter

# 회귀 테스트 (Windows 로컬은 pytest foreground 로만 — background 255 회피)
python -m pytest benchmark/tests/test_cross_adapter_e2e.py -v
```

```python
from benchmark.reference import run_e2e
receipt = run_e2e()          # root=None → 안전 임시 root 자동 생성·정리
assert receipt["decision"] == "GO"
```

`run_e2e` 는 `benchmark.result.ScenarioResult`/`summarize` 로 만든 receipt(dict)를 반환한다.
`decision` 은 `FAIL==0 && operating_fingerprint_equal && total_matches_expected` 일 때만 `GO`.

## 무엇을 증명하나 (2-proc-shared-home)

두 어댑터가 **하나의 공유 격리 홈**을 공유하고, 그 공유물이 `ledger.sqlite` **단일 버스** 하나임을 단언한다.
이 패턴은 이 repo 최초라 동작을 가정하지 않고 **로컬 + CI green 이 유일한 증명**이다.

| id | 단언 |
| :-- | :-- |
| `E2E-A-SAVE` | 모델 A 사람 승인 저장(target+distractor2+hard-neg) → active 증가·target node_id 획득 |
| `E2E-NOAUTH` | CLAUDECODE=1(AI) save 거부 · active 불변 (AI 는 제안까지만) |
| `E2E-B-RECALL` | 모델 B 새 프로세스 recall → target 회상 · distractor/hard-neg 배제 |
| `E2E-B-EXPLAIN` | 모델 B explain(A 의 node_id) 근거 연결 + 존재않는 id 실패(negative control) |
| `E2E-BUS` | 공유물=`ledger.sqlite` 단일 · reader 는 write cap 미선언 |
| `E2E-KAT` | canonicalization drift 게이트 (아래 참조) |
| `E2E-HOSTED` | Hosted 최종저장=로컬 commit_bundle 수렴 (실 worker 미실행 → `UNSUPPORTED`) |

fixture 와 recall/explain 단언은 기존 자산을 그대로 재사용한다 —
`benchmark.scenarios.mgb_08`(target+distractor2+hard-neg)·`mgb_06`(explain + negative control).

## digest 결정성 게이트 — check_vectors 위임

canonicalization digest 결정성(spec ↔ 구현 drift)은 **core 내부 심볼을 재구현·재-import 하지 않는다.**
`_run_check_vectors()` 가 [docs/memory-pr/tools/check_vectors.py](../../docs/memory-pr/tools) 를
**subprocess 로 실행**하고 `exit 0` 을 GO 로 위임한다(`E2E-KAT`). 이유:

- canonicalization 은 프로필별 필수 계약이라 산식은 정본(check_vectors)이 소유한다 — 여기서 복제하면
  drift 의 두 번째 원천이 된다.
- check_vectors 는 실제 binggupack 함수로 재계산해 고정 KAT(`vectors/kat/*`)의 expected 와 대조하며,
  illustrative-only vector(사람 기원·실서비스 의존)는 재현 불가라 개수만 보고(UNSUPPORTED)한다.

subprocess 환경은 `CLAUDECODE` 를 unset 하고 `PYTHONUTF8=1` 로 고정한다.

## 결정성·안전 경계

- **결정성** — 공유 격리 홈은 `semantic_recall` 기본 OFF(config 부재→False, Ollama/bge-m3 미사용)라 회상은
  순수 lexical. 오케스트레이터·테스트에 `sleep`/`random`/wall-clock 단언 0.
  freshness 만료·tamper·실 worker 는 이 참조구현의 단언 대상이 아니다(UNSUPPORTED/illustrative).
- **격리 홈 = 운영 홈 비접촉** — 공유 홈은 `runner._make_safe_root`/`_assert_home_isolated` 로만 만든다
  (운영 홈 `~/.binggupack` 상하위·symlink 거부). 격리는 `BINGGU_HOME` env 로만 주입(`--home` 미사용).
  운영 홈 ledger fingerprint(`contracts.operating_fingerprint`/`fp_content_equal`)의 **실행 전후 불변**을
  하드게이트로 검증한다.
- **공유홈 정합** — writer subprocess A 는 blocking 반환(commit)한 뒤에만 reader B 가 기동한다.
  A/B 동일 `BINGGU_HOME`, A→B 사이 rmtree 금지. reader B 는 write cap 미선언(RECALL/EXPLAIN caps 만).

## MGB(계약) vs reference(참조구현)

| | MGB (`benchmark/` 상위) | reference (`benchmark/reference/`) |
| :-- | :-- | :-- |
| 성격 | **vendor-neutral 계약** — 임의 시스템을 adapter 로 붙여 평가 | **BingguPack 참조구현** — 계약을 실제로 도는 로컬 예시 |
| 산출 | 시스템별 PASS/FAIL/UNSUPPORTED 결과표 | cross-adapter E2E receipt(GO/FAIL) |
| 대상 | black-box 공개 CLI 프로필(제품 무관) | 두 BingguPack adapter(writer/reader) 공유 홈 |

MGB 는 "이 계약이 특정 제품에 종속되지 않음"을 `toy_conforming`/`toy_failing` 으로 보이고,
reference 는 "그 계약을 실제로 만족하는 구현이 존재함"을 로컬 프로세스로 보인다. 둘은 상호보완이다.

## 백링크 (Memory PR Spec)

라인번호·SHA 가 아닌 **문서·섹션 제목**으로만 참조한다(정본이 정본을 소유):

- [docs/memory-pr/README.md](../../docs/memory-pr/README.md) — "Memory PR 이란", "공통 불변식 (전 프로필)"
- [core-model-v0.1-draft.md](../../docs/memory-pr/core-model-v0.1-draft.md) —
  "5. canonicalization 은 프로필별 필수 계약", "7. Recall / Explain 는 logical model"
- [mgb-crosswalk.md](../../docs/memory-pr/mgb-crosswalk.md) — "1. 책임 분리", "5. check_vectors.py 의 책임"
- [security-limitations.md](../../docs/memory-pr/security-limitations.md) —
  "5. approve 이벤트 자동생성은 UNSUPPORTED", "6. freshness / 만료"
- [hosted-relay-profile-v0.1-draft.md](../../docs/memory-pr/hosted-relay-profile-v0.1-draft.md) —
  Hosted 최종저장 commit_bundle 수렴(로컬 커밋경로 단언 · 실 worker illustrative)
