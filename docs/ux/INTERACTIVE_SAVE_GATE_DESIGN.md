# Interactive Save Gate — Design (Lane C)

> **상태: 설계만.** 기존 confirm-phrase safety model을 절대 바꾸지 않는 *보조* 옵션 설계입니다.
> 실제 구현은 owner 승인 후. 이번 사이클에서는 코드 변경 0.

## 불변 안전 조건 (절대 유지)

- 마지막 human confirmation 유지 — 키보드로 직접 친 `SAVE n` / `DEPRECATE 3 704c2864` 같은 explicit confirm phrase **제거 금지**.
- AI autosave 금지 · `G4_no_auto` 유지 · `save_candidate(dry_run=false)` actual save 차단 유지.
- interactive mode가 production write로 이어지면 안 됨.
- 기본값(default)은 기존 non-interactive / explicit confirm. CI·자동화는 기존 방식 그대로.

## 문제

confirm phrase(`SAVE 1,2`, `DEPRECATE 3 <id8>`)는 위조 불가·감사가능하지만, 사람이 번호·id8을 손으로 옮겨 적어야 해 진입장벽이 있습니다.

## 설계: interactive는 "입력 보조"일 뿐, 게이트는 그대로

interactive mode는 **사람이 confirm phrase를 구성하도록 돕는 TTY 래퍼**입니다. 게이트(0-A human gate, G4)는 동일하게 통과해야 합니다.

```bash
binggu.py save-candidate --interactive
```

흐름:
1. preview 후보를 번호와 함께 표시 (기존 `capture preview`와 동일, 저장 0).
2. 사용자가 화살표/번호로 항목 선택 (TTY 입력).
3. 도구가 **최종 confirm phrase를 화면에 그대로 출력**하고, 사용자가 **직접 한 번 더 타이핑/Enter로 승인**해야 실제 게이트로 전달.
4. 게이트는 기존과 동일: actor=human, phrase 정확 일치, PII 재스캔, `G4_no_auto`.

핵심: interactive는 **번호 선택까지만 보조**하고, 마지막 승인 행위(키보드 confirm)는 절대 자동화하지 않습니다. AI/TTY가 phrase를 대신 "확정"할 수 없습니다.

## 안전 가드 (구현 시)

- `--interactive`는 **TTY에서만** 활성 (`sys.stdin.isatty()`). 비-TTY(CI·pipe·AI tool_use)에서는 자동 비활성 → 기존 explicit 방식 강제.
- MCP 경로에서는 interactive 미노출 (MCP는 dry-run/read + write-gated만, 사람 TTY 없음).
- interactive로 구성된 phrase도 동일 게이트 통과 — 우회 경로 0.
- `actor=auto` 또는 stdin이 사람 타이핑이 아니면 BLOCK 유지.

## MCP 관점

MCP는 사람 TTY가 없으므로 interactive save를 노출하지 않습니다. `save_candidate`는 현행대로 dry-run preview + write-gated(G4_no_auto BLOCK) 유지. interactive는 로컬 CLI 전용 보조 UX입니다.

## 판정

**Lane C = 설계 완료, 구현 보류.** 최소 prototype도 save gate 로직에 손대므로, 안전 검증(게이트 우회 0 회귀) 없이는 미구현. owner 승인 + 회귀 테스트 후 Phase 구현 권장.
