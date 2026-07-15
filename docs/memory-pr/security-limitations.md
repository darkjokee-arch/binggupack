# Memory PR — Security & Limitations — v0.1-draft

> 정직한 한계 명세. 이 문서는 BingguPack 이 **보장하지 않는 것**을 분명히 한다.

## 1. 위협 모델 경계

- 이 통제(모델은 제안만·사람만 확정)는 **"모델 tool surface 가 approval store 에 write 하지 못한다"** 가 성립하는 배포에서만 하드하다 — 웹/앱 커넥터, 잠긴 에이전트 등.
- **Filesystem / Bash MCP 를 동반한 호스트는 위협 모델 밖이다.** same-host attacker 는 로컬 파일(ledger.sqlite·approvals.jsonl·save_gate_log.jsonl)을 직접 조작할 수 있다.

## 2. "AI 위조 불가" 의 범위 (과장 금지)

Interactive Save 의 승인은 3모드다:

| 모드 | 위조 난이도 |
| :-- | :-- |
| `save_gate_ref` | AI 는 UserPromptSubmit hook 을 발화할 수 없어 이 모드로 승격 불가 — **이 모드 한정 위조 불가** |
| `cli_command` | CLAUDECODE 없는 직접 터미널 입력 → **hook 없이 human 판정**(isatty 무관). 셸 접근 주체를 human 으로 가정 |
| `denied` | `CLAUDECODE` truthy → reader(거부 전용) |

- ⚠️ `CLAUDECODE` 는 **제거 가능한 소프트 신호**(환경변수)지 암호 증명이 아니다.
- 따라서 **"AI 위조 불가" 는 `save_gate_ref` + `CLAUDECODE` 세션 조합에서만 성립**한다. 전역 주장이 아니다.

## 3. tamper-proof 아님

- `trusted_approval` 은 서명/HMAC 을 도입하지 않았다. **같은 머신의 키 = 보안 연극**이라는 판단(정직).
- 변조 탐지(`binding_mismatch:payload/operation/ledger/protocol`)는 **소비 시도의 부수효과로만** 발생한다. 임의 payload/nonce/receipt 를 받아 독립 검증하는 **standalone tamper-verify CLI 는 없다**(→ MGB-10 UNSUPPORTED).

## 4. Hosted 본문 평문 잔존 (중요)

Hosted Relay 경로에서 본문 평문은 **세 곳**에 존재한다:

1. hosted DO storage 체류 (drain 시 delete).
2. 로컬 staging 원문.
3. **commit 후 원문 전체가 `_archive/<intent_id>.processed.json` 으로 이동·영구 보존** (hosted 계약 15 · 삭제가 아니라 이동 · 별도 owner purge 만 삭제).

- ledger(nodes) 는 전문을 저장하지 않지만(발췌 + 해시), **로컬 평문은 영구 잔존**한다.
- **본문 암호화 없음** — 전송 TLS + 인출 HMAC 무결성만. 체류 중 평문. 보완책은 PII 를 preview 단계에서 제외 + 짧은 TTL + pull 후 inbox 잔존 0 뿐이다.

## 5. approve 이벤트 자동생성은 UNSUPPORTED

- Trusted approve 발행은 **대화형 TTY 전용**(비대화형 exit 2 · Unix PTY 테스트만 · Windows PTY 미지원으로 skip · test_double 채널은 배포 wheel 에서 제거).
- 따라서 **공개 CLI / CI 로 approve 이벤트를 스크립트로 만들 수 없다.** Trusted Event 소비 vector 는 `illustrative-only` 로만 제공한다.

## 6. freshness / 만료

- Interactive `GATE_WINDOW` 기본 1시간 · Trusted `DEFAULT_TTL` 900초.
- 만료(stale) 관찰은 **wall-clock 실대기**가 필요하고 `--now` 주입 CLI 플래그가 없어, 공개 CLI 로 결정적 재현이 비현실적이다(→ MGB-03 UNSUPPORTED).

## 7. 요약: UNSUPPORTED 목록

approve 이벤트 자동생성 · MGB-03(stale approval 결정적 재현) · MGB-10(public tamper verification) · Hosted 본문 암호화 · preview_ref 의 CLI 출력 · 실 worker HMAC 왕복/DO drain.
