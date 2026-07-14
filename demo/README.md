# Memory PR — 30-second demo

`binggu demo` shows the whole BingguPack idea in one **offline, reproducible** run:

> **AI proposed 3 memories. You approved 1. A fresh process recalled exactly that 1.**

- **No network, no API key.**
- Runs in an **isolated temporary ledger** — your operating ledger (`~/.binggupack/ledger.sqlite`) is never written.
- Recall is verified by the **content digest** of the approved memory (sha256 of the exact text), not a substring match — and the demo **fails** if the child-process recall fails. There is **no silent same-process fallback**.
- Approved: 1 candidate. The other 2 candidates are **not** stored and do **not** appear in recall.

> No animated GIF is required to understand or verify this demo — the script below reproduces the exact output on any supported platform. A hand-made GIF, if present, is a **reference asset only** and never gates CI or the docs.

## Run it

```bash
pip install binggupack
binggu demo --non-interactive        # or: bash demo/memory-pr-demo.sh  /  pwsh demo/memory-pr-demo.ps1
```

## Actual output (reproducible — only the temp path varies)

```text
============================================================
BingguPack 데모 — AI가 기억해도, 결정권은 나에게
============================================================
격리 데모 장부: <isolated-temp>/ledger.sqlite
(운영 장부·기억 데이터에는 쓰지 않습니다 · 운영 홈: ~/.binggupack)

[1] 대화에서 기억 후보를 발견했습니다 (아직 저장 안 함):
    입력: "저는 앞으로 답변을 결론부터 짧게 받는 걸 선호합니다. 매주 금요일 오후에는 주간 회고를 하기로 정했어요. 결제 배포 전에는 스테이징에서 먼저 검증하기로 했어요."

    [1] (판단) 저는 앞으로 답변을 결론부터 짧게 받는 걸 선호합니다.
    [2] (증거) 매주 금요일 오후에는 주간 회고를 하기로 정했어요.
    [3] (판단) 결제 배포 전에는 스테이징에서 먼저 검증하기로 했어요.

    현재 활성 기억: 0개 — 승인 전에는 아무것도 확정되지 않습니다.

[2] (비대화형) '스테이징' 이(가) 든 후보 [3] 만 승인합니다 — 데모 격리 홈에서만 시뮬레이션.

[3] 승인한 항목만 로컬 장부에 확정 기록했습니다.
    ✓ 저장 1개 — 활성 기억 0 → 1
      · 결제 배포 전에는 스테이징에서 먼저 검증하기로 했어요.
    ✗ 고르지 않은 후보는 저장되지 않음: 저는 앞으로 답변을 결론부터 짧게 받는 걸 선호합니다.
    ✗ 고르지 않은 후보는 저장되지 않음: 매주 금요일 오후에는 주간 회고를 하기로 정했어요.

[4] 새 프로세스에서 회상 — "결제 배포 스테이징 검증"
    (자식: python -m binggu recall · same-process fallback 없음)
    ✓ 새 프로세스가 승인한 기억을 회상 — content digest 일치
      memory-digest(sha256) = c800eab79d495e359b051070cbdf73c08c9575ad05d5fe9461991c4ac58c25c6
      # 회상(Hot 색인 · 상위 1) — "결제 배포 스테이징 검증" (랭킹순 · candidate · read-only · 원본 스캔 0)
        1. (judgment [결정] rank=1.600 rel=1.00 trust=0.5) 결제 배포 전에는 스테이징에서 먼저 검증하기로 했어요.
      관련 기억 1건(Hot 색인·랭킹순). candidate — 사람 확정 전 참고용.
    ✓ 승인하지 않은 후보 2건은 회상 결과에 없음

[5] 이 기억이 무엇에 근거하는지 확인 (provenance):
    기억: 결제 배포 전에는 스테이징에서 먼저 검증하기로 했어요.
    근거: 원문 발화에서 캡처(evidence_supports 연결) · memory-id = node:CONV:c800eab7
    더 보기:  binggu explain node:CONV:c800eab7
============================================================
```

The `memory-digest` and the `memory-id` suffix (`c800eab7`) are deterministic — they are derived from the exact approved text, so a correct run always reproduces them.

## Inspect it afterwards

```bash
binggu demo --non-interactive --home ./_memory_pr_demo_home --keep
binggu --ledger ./_memory_pr_demo_home/ledger.sqlite list          # only the approved memory
binggu --ledger ./_memory_pr_demo_home/ledger.sqlite explain <memory-id>
```

## Scenario source of truth

The conversation, the approved candidate, and the recall query live in a single constant — `DEMO_SCENARIO` in [`binggu.py`](../binggu.py) — which **both** the demo and [`tests/test_demo.py`](../tests/test_demo.py) read. The approved candidate is chosen **by content** (`approve_marker`, i.e. `스테이징`), not by a fixed index, so the demo does not depend on classifier ordering. A regression test (`test_demo_fails_when_child_recall_fails`) forces the child recall to fail and asserts the demo returns non-zero — the old "looks like it recalled" false gate cannot come back.
