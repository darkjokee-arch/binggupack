# chat feedback button UX 설계 (E — 설계만, 구현 금지) — 2026-06-11

> owner 범위: 설계만. save/write 들어가는 구현은 **별도 GO**. 엔진(G3 resolve_review: 성공/실패/불확실/판정불가 + 사유 필수 + 노드 무변)은 이미 staging에 존재 — 이 문서는 채팅 쪽 껍데기 설계.

## 1. "버튼"의 실체 (채팅/MCP 제약)
- Claude·ChatGPT 커넥터 채팅에는 네이티브 버튼 위젯이 없음 → **버튼 = 도구 응답이 제시하는 고정 선택지 4개 + 사용자의 한 줄 답 + 모델의 (미래) 도구 호출**.
- 4값은 G3 enum과 1:1 고정: `성공 / 실패 / 불확실 / 판정불가` — 채팅과 엔진이 같은 어휘를 쓰면 매핑 레이어가 필요 없음.

## 2. UX 흐름 (3턴)
1. **제시**: (미래 도구 또는 리마인드 목록이) 판단 1건 표시 —
   > "판단: '마진 낮으면 보류한다' (2026-06-10 검증 예정이 지났습니다)
   > 결과를 선택해 주세요: ① 성공 ② 실패 ③ 불확실 ④ 판정불가 — 선택 + 한 줄 이유"
2. **선택**: 사용자가 "② 실패, 실제 낙찰가가 예상보다 높았음"
3. **기록**(미래, 별도 GO): 모델이 `judgment_feedback_record` 호출 → staging `resolve_review`(기록만, 자동 강등 0) → 응답: "기록됨. 노드 상태는 변하지 않았습니다. 기각하려면 따로 말씀하세요."

## 3. 미래 tool 스키마 초안 (구현·노출 금지 — write 계열)
```json
{ "name": "judgment_feedback_record",
  "inputSchema": { "type": "object", "properties": {
      "node_id": {"type": "string"},
      "outcome": {"type": "string", "enum": ["성공", "실패", "불확실", "판정불가"]},
      "reason": {"type": "string", "description": "필수 — 한 줄 이유"} },
    "required": ["node_id", "outcome", "reason"] } }
```
- annotations: readOnlyHint=**false** (write) → hosted 노출 시 별도 인증·게이트 설계 의무.

## 4. 안전 규칙 (설계 고정)
- outcome을 모델이 추론으로 채우는 것 금지 — **사용자 발화에 명시된 선택만** (모델 프롬프트 규칙 + 서버에서 reason 필수로 이중 방어)
- 기록 ≠ 강등: "실패"여도 노드 state/candidate 무변 (G3에서 checksum으로 이미 증명). 강등은 기각 도장(사유 필수) 별도 행동
- 미응답 시 아무 일도 안 일어남 (리마인드는 다음 due 조회 때 다시 표시될 뿐)

## 5. 구현 전제 (별도 GO 때 체크리스트)
① hosted에 write 도구를 노출하는 첫 사례 — 경로 토큰만으로 충분한가(인증 상향 검토 의무) ② staging 원격 접근 구조(현재 staging은 로컬 SQLite — hosted에서 기록하려면 전송 경로 설계 필요) ③ audit chain 연동 ④ rollback. **이 4개가 풀리기 전 구현 착수 금지.**
