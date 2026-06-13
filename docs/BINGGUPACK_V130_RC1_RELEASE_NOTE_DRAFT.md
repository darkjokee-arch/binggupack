# BingguPack v1.3.0 — Release Note (DRAFT, 커밋/푸시 전)

> 상태: **draft**. 커밋·푸시·태그 미실행. MCP 클라이언트 실등록·hook 등록 0.

## 한 줄
자동 캡처 판정 + preview를 **read-only MCP tool 2개**로 노출 (로컬/수동, write 없음).

## 버전
- 이전: v1.2.0
- 이번: **v1.3.0** (minor — 하위호환 기능 추가, 기존 도구/스키마 변경 0)

## 추가 (Added)
- `scripts/binggu_capture_classifier.py` — 발화→판정 순수 함수(state/confidence/pinned/signals). 2-게이트(preview_trigger→veto→판단신호).
- `scripts/binggu_capture_buffer.py` — 메모리 candidate 버퍼 + batch preview 렌더(pinned 상단, weak 표시).
- `scripts/binggu_capture_session.py` — 세션 entrypoint(`on_user_prompt`/`on_session_end`). hook 미등록.
- `scripts/binggu_capture_cli.py` — 수동 호출 경로(stdin/`--feed`).
- MCP read-only tool 2개:
  - `capture_classify(utterance, prev_turn?)` → 판정 dict (발화 원문 미반환)
  - `capture_preview(utterances[])` → 무상태 재구성 후 후보 preview

## 설계 원칙 (변경 없음, 재확인)
- **캡처 트리거 ≠ preview 트리거**: "이거 저장해"=pinned candidate(즉시 preview X), preview는 "빙구팩 저장해"/세션말에만.
- **evidence는 캡처 게이트 아님** — confirmed/회수 게이트. 없어도 candidate 가능.
- 노드 5종 + 동사형 typed edge + evidence 증빙 스키마(v1.1.0) 유지.

## 명시적 비포함 (HOLD)
- ❌ ledger write / active 저장 / owner approval 흐름
- ❌ OpenCrab push·export
- ❌ hook 실등록(UserPromptSubmit/Stop)
- ❌ MCP 클라이언트 실등록(`.claude.json`)
- ❌ 판매/marketplace (개인용 트랙만)

## 검증
- handlers `--selftest`: GATE=GO (12 케이스, capture 2종 포함, raw 미유출)
- mcp_server 프로토콜 `--selftest`: GATE=GO (registration NOT_DONE)
- capture 4모듈 셀프테스트: 전부 GO
- 개인정보/민감정보 정적 스캔: 0건 (synthetic only)
