# BingguPack 세션 마무리 트리거 — 설계 + CLAUDE.md 규약 초안

> 빙구팩은 거버넌스 자산(박제/CLAUDE.md/정책파일)에 write 0(read-only). 본 문서는 **초안 제공**이며
> 실제 CLAUDE.md 반영은 사장님 승인 후 사람이 직접 한다(빙구팩 자동수정 0).

## 1. 무엇인가

세션 마무리 자연어(사용자마다 다름 — "오늘 여기까지", "마무리하자", "끝", "수고했어" 등)를
**모델이 의미로 감지**(키워드 매칭 X)한 신호를 받아, 세션 끝에 자동으로:

1. **저장 preview** — capture 버퍼 candidate 목록(active 아님 · 저장 0)
2. **거버넌스 정리** — 대비 기록·적중률(owner/ai 양쪽 · 도메인 분리) 신호 요약

을 사람에게 표시한다. **저장은 사람이 직접** — preview 를 보고 `SAVE n`(정확한 번호) 타이핑 시에만
기존 save_gate 로 진행. 헌법 자동저장 0 유지.

## 2. 구현

- 모듈: `scripts/binggu_session_close.py` (stdlib only · 저장 0 · read-only)
  - `detect_session_close(signal, home)` — 모델 의미감지 신호 + opt-in 사용자 등록 표현 처리
  - `register_close_phrase(phrase, home)` — 사용자별 마무리 표현 등록(opt-in · close_phrases.json)
  - `build_close_summary(home, cwd, ledger_path)` — preview + 거버넌스 요약 빌드
  - `render_close_md(summary)` — 결정적 마크다운(LLM 0)
  - `process(signal, ...)` — 감지→요약→렌더 통합
- 기존 구조 정합: capture_hook(자동 후보 수집) · save_gate_hook(사람 SAVE) 흐름과 동일 경계.
  preview 는 `binggu_capture_persist.PersistentCaptureBuffer.render_preview` 재사용(candidate-only),
  거버넌스는 `binggu_hit_stats.both_sides / proposal_priority_signal` 재사용(signal_only 표지).
- selftest: `python scripts/binggu_session_close.py --selftest` → GATE=GO (15/15 PASS).

## 3. 감지 = 모델 의미감지 (CLAUDE.md §9 Layer1 정합)

빙구팩은 **자유문자열 키워드로 추정하지 않는다**. 감지 원천 2개:

1. **1차 — 모델 의미감지**: 호출측(Claude 모델 행동 규약)이 발화 의미를 Layer1 으로 판정해
   `signal["model_detected_close"]=True` 를 넘긴다. 사용자마다 다른 마무리 표현을 의미로 이해.
2. **보조 — 사용자 등록 표현(opt-in)**: `close_phrases.json` 에 등록한 표현과 **정확 일치**할 때만
   (부분 키워드 매칭 금지). 사용자가 자기 마무리 말투를 등록할 수 있는 옵션.

둘 다 없으면 `is_close=False`(graceful · 표시 0).

## 4. CLAUDE.md 규약 초안 (§9 Layer1 하위 또는 §8-1 — 사람 승인 후 사람이 직접 반영)

```
### 세션 마무리 발화 감지 (Layer1 의미감지 정합 · 저장 0)

빙구팩 capture 가 활성(opt-in)인 세션에서, 사용자 발화가 **세션 마무리 의도**로 의미감지되면
(사용자마다 표현 다름 — "오늘 여기까지"·"마무리하자"·"끝"·"수고" 등 · 키워드 매칭 X / Layer1 의미 이해):

① 저장 preview(capture 버퍼 candidate · active 아님) + 거버넌스 정리(대비 기록·적중률 신호)를
   자동 표시한다(binggu_session_close.process — read-only · 저장 0).
② 저장은 **사람이 직접** — preview 를 보고 `SAVE n`(정확한 번호) 타이핑 시에만 기존 save_gate 로
   진행. 빙구팩 자동저장 0 · 헌법 자동저장 0 유지.
③ 적중률은 '신호'(상관≠인과) — 규칙/박제 자동교체 근거 아님(자동결정·자동교체 0).
④ 사용자별 마무리 표현은 close_phrases.json 에 opt-in 등록 가능(정확 일치 · 부분 키워드 금지).

Layer1 정합: 마무리 요약 표시 = 사장님께 선택지·신호 제시(조회+신호)이지 '결정 요청'이 아님
→ pre-action-risk-check Hook 차단 대상 아님(read-only · write 0).
```

## 5. 헌법 준수 (selftest 증명)

- candidate-only · 사람 승인 게이트 · PII 제외 · audit chain · 안전 양보불가 · AI 추천만(자동결정 0)
- 저장 0 (T7~T11) · ledger write 0(mtime 불변 T10) · candidate-only ledger 미생성(T9)
- 거버넌스 자산(박제/CLAUDE.md/정책) write 0 (T12b)
- stdlib only(hashlib/json) · 외부 바이너리 0 · graceful(빈 버퍼/무 ledger/잘못된 입력 T12a)
