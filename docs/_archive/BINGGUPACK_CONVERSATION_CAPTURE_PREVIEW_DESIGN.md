# conversation_capture_preview 설계 (A) — 2026-06-11, 병렬 GO

> **저장 0 원칙이 정체성**: 이 도구는 "대화에서 뭐가 노드가 될 수 있는지 미리 보여주기"까지만 한다.
> 실제 등재는 로컬 파이프라인(G0 분류→연필→묶음 승인→볼펜)과 C-2 승인 게이트의 몫.
> conversation_candidate_save 는 별도 GO 전 **구현 금지** (owner 금지선).

## 1. 입력 경계 (ChatGPT/Claude 채팅) — D 조사로 확정 (2026-06-11)
- **명령형만 가능 (신뢰도 높음)**: 사용자가 "이 대화 캡처 미리보기 해줘" → 모델이 대화 일부를 **tool 인자(text)로 재생성해 전달**. 메모리 MCP 생태계가 이 패턴으로 실동작 중. 서버는 tools/call 인자 외에 대화 접근 수단 없음.
- **자동 관찰 불가 (신뢰도 높음)**: MCP 스펙 전 버전(2025-03-26/06-18/11-25)에 대화 push 메커니즘 없음 + claude.ai/ChatGPT 모두 tools만 지원(sampling/roots/elicitation 미지원) + 대화 자동 첨부 옵션 양사 없음. daemon/hook 금지선과 무관하게 **구조적으로 불가**.
- **verbatim 미보장 (주의)**: 인자는 모델이 출력 토큰으로 재생성한 텍스트 — 원문 그대로 보장 없음(요약·누락 가능, ChatGPT 쪽이 요약 경향 더 높을 것으로 추측 — 실측 필요). → preview 출력에 "모델 전달분 기준" 한계 표기 권장.
- 인자 크기 공식 상한 미공표 — 실질 = 모델 출력 토큰 한도. 입력 캡: 20,000자 — 초과분 줄 경계 절단 + 표기.

## 2. tool schema (hosted 노출은 별도 GO — 지금은 로컬 사양만)
```json
{ "name": "conversation_capture_preview",
  "inputSchema": { "type": "object", "properties": {
      "text": {"type": "string", "description": "캡처 후보를 뽑을 대화 발췌 (사용자가 명시적으로 전달)"},
      "max_candidates": {"type": "integer", "description": "기본 10, 최대 20"} },
    "required": ["text"] },
  "outputSchema": { "type": "object", "properties": {
      "candidates": {"type": "array", "items": {"type": "object", "properties": {
          "sentence": {"type": "string"}, "label_kind": {"type": "string"},
          "rule_id": {"type": "string"}, "a0_verdict": {"type": "string"},
          "candidate": {"type": "boolean"} } } },
      "excluded_counts": {"type": "object"},
      "truncated": {"type": "boolean"},
      "preview_markdown": {"type": "string"},
      "nothing_saved": {"type": "boolean"} },
    "required": ["candidates", "excluded_counts", "preview_markdown", "nothing_saved"] },
  "annotations": {"readOnlyHint": true, "destructiveHint": false, "idempotentHint": true, "openWorldHint": false} }
```

## 3. 처리 파이프라인 (전부 기존 모듈 재사용, deterministic·LLM 0)
문장 분리(종결어미/개행) → `_meaningful` 필터(mvp2 기준: 6자/12자) → **PII/secret 검출 시 해당 문장 후보 제외**(scan_residual_pii + SECRET_PATTERNS — redact가 아니라 제외, 사유는 kind 카운트만) → 중복 dedup(정규화 hash) → G0 5종 분류(lkmap) + A0 헌법 shadow 판정 → 상위 max_candidates.

## 4. 출력 포맷
표(문장 전체 · 도장 5종 · 분류 근거 · 헌법 판정) + 제외 통계(kind: count만, raw 0) + 푸터 고정:
> "미리보기일 뿐 아무것도 저장되지 않았습니다(nothing_saved=true). 등재는 로컬 승인 게이트에서만."

## 5. 저장 0 원칙 (구현 강제)
- FS write 0 · DB write 0 · 로그 적재 0 — 순수 함수(반환만), selftest가 FS 전후 대조로 증명
- **raw 대화 전체 재출력 금지** — 출력에는 선별된 후보 문장 발췌만 (입력 전문이 출력에 포함되지 않음을 selftest로 증명)
- candidate=true 고정 표기 · confirmed 단어 출력 0

## 6. 단계 분리 (owner 정합)
| 단계 | 상태 |
|---|---|
| A 설계 + B 로컬 구현 + C selftest | **이번 GO** |
| D 커넥터 적용 가능성 결론 | 이번 GO (조사) |
| preview의 hosted(TS) 포팅·live 노출 | 별도 GO |
| conversation_candidate_save (저장) | 별도 GO (write 계열) |
