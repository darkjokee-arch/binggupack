# Memory Governance Benchmark (MGB) v0.1

**AI 기억 거버넌스를 평가하는 재현 가능한 공개 기준.** 기능 수를 겨루는 벤치마크가 아니라,
"AI가 기억을 어떻게 안전하게 다루는가"의 계약을 검증한다.

- **black-box 공개 CLI 프로필** — 대상 시스템의 공개 인터페이스만 호출하고, verdict 는 runner 가
  관찰 자료(exit code·구조화 상태)로 독립 판정한다.
- **정직한 한계** — 공개 CLI 로 독립 검증할 수 없는 항목은 PASS 로 위장하지 않고 `UNSUPPORTED` 로 남긴다.
- **이식 가능** — `toy_conforming`(전부 통과)·`toy_failing`(계약 위반은 실제 FAIL)이 계약이 BingguPack
  전용이 아님을 증명한다.

전체 규격은 [SPEC.md](SPEC.md) 참조.

## 실행

```bash
# BingguPack (공개 CLI 프로필)
python -m benchmark.runner --adapter binggupack

# 참조 adapter — 계약이 특정 제품에 종속되지 않음을 보여줌
python -m benchmark.runner --adapter toy_conforming   # 12 PASS
python -m benchmark.runner --adapter toy_failing      # 지정 시나리오에서 FAIL
```

결과는 `benchmark/results/<adapter>.json` 에 기계판독 형태로 저장된다.

## 결과 축

| execution_status | verdict |
|---|---|
| OK / ERROR / UNSUPPORTED / SKIPPED | PASS / FAIL / UNSUPPORTED / NOT_RUN |

`ERROR·UNSUPPORTED·SKIPPED` 는 PASS 로 집계하지 않는다. summary 는 12개 전부를 명시하며 분모를 축소하지 않는다.

## 12 시나리오

MGB-01 비승인 활성 기억 차단 · 02 exact preview binding · 03 stale approval 거부 ·
04 replay 거부 · 05 speaker provenance · 06 evidence explain · 07 supersede(이력 보존) ·
08 새 프로세스 동일 정본 회상 · 09 remote intent 로컬 write 차단 · 10 tamper detection ·
11 candidate ≠ active · 12 운영 홈 격리. (계약·PASS 규칙은 [SPEC.md](SPEC.md) §3.)

> MGB-10 은 BingguPack v0.1 공개 CLI 프로필에서 `UNSUPPORTED` 다 — 이유는 SPEC.md §5.

## 다른 시스템 adapter 작성

`adapters/base.py` 의 `Adapter` Protocol 을 구현한다.

```python
class MyAdapter:
    name = "my-system"
    def capabilities(self): ...          # 공개 인터페이스로 지원하는 Cap 집합
    def new_home(self, root): ...        # 허용 임시 root 하위 격리 홈
    def operating_fingerprint(self): ... # 운영 정본 사후 오염 sentinel (없으면 None)
    def observe(self, home, op, **kw): ...# op 실행 → Observation(관찰 자료만, verdict 계산 금지)
    def cleanup(self, home): ...
```

`capabilities()` 에 없는 op 을 요구하는 시나리오는 자동으로 `UNSUPPORTED` 가 된다.
**실제로 adapter 를 구현·실행한 증거가 없는 제품은 결과표에서 `NOT_EVALUATED` 로만 표기한다**
(근거 없는 ❌·경쟁 제품 비방 금지).

## 안전 경계

- 매 시나리오 새 격리 홈(허용 임시 root 하위 realpath · symlink/junction 거부).
- tamper 시나리오는 벤치마크가 만든 **합성 장부만** 대상으로 한다(운영/실제 장부 미접촉).
- 운영 정본 fingerprint 는 실행 전후로 비교하는 **사후 오염 sentinel** 이며, 바뀌면 hard FAIL 신호다.
  v0.1 은 운영 HOME 전체 쓰기 차단을 약속하지 않고 검증 가능한 **운영 ledger 불변**만 주장한다.

## 상태

v0.1. 향후(별도 Wave): adapter-mediated white-box 프로필 · JSON Schema 정본 · MGB-10 공개 검증
인터페이스(코어 변경 · owner 승인 필요) · 실제 cross-model E2E · 타사 제품 실측 결과표.
