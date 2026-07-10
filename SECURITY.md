# 보안 정책 (Security Policy)

## 취약점 신고

보안 문제를 발견하시면 **공개 이슈로 올리지 마시고** 아래 비공개 채널로 알려주세요:

- GitHub 비공개 신고: [Security Advisories](https://github.com/darkjokee-arch/binggupack/security/advisories/new)

가능하면 재현 방법과 영향 범위를 함께 주시면 빠르게 확인하겠습니다.

## 위협 모델 (무엇을 지키고, 무엇은 범위 밖인가)

빙구팩은 **개인이 자기 PC에서 쓰는 로컬 우선(local-first) 도구**입니다. 보안 경계는 이 전제 위에 설계됐습니다.

### 지키는 것 (in scope)

- **민감정보 유출 차단** — 비밀번호·인증서·PII·개인사(예: 이혼)는 애매하면 무조건 제외합니다(미리보기·회수·팩 업로드 전 구간에서 강제).
- **승인 없는 장부 확정 방지** — 내 기억 장부에 **확정(active) 저장**되는 것은 사람의 명시 승인을 거칩니다. 승인의 근거는 **모델이 재현할 수 없는 대역 외(out-of-band) 앵커**입니다: 내가 키보드로 친 `SAVE n` 발화를 UserPromptSubmit hook 이 기록한 `save_gate_log`, 또는 CLI 의 TTY 입력. AI 가 preview 응답의 확인 문구(`SAVE 1` 등)를 그대로 **재현**해도 그것만으로는 사람 승인이 되지 않습니다. MCP 로 들어오는 저장/기각/교체(pair·deprecate·replace·mark)는 사람 앵커가 없으면 **fail-closed(G4_no_auto)** 로 차단됩니다. (기억할 만한 발화가 **임시 후보(candidate)** 로 자동 수집되는 것과, 그 후보를 **장부에 확정**하는 것은 별개입니다 — 확정만 승인이 필요합니다.)
  - **한계(정직).** 로컬 클라이언트 UI 가 실제 사람 클릭을 보장하지 못하는 환경에서는, "확인 문구를 받았다"는 사실만으로 사람 승인을 증명할 수 없습니다. 빙구팩은 이 경우 **자동 승격을 하지 않는(fail-closed)** 쪽을 택합니다. 후보 집합에 바인딩된 만료·1회용 승인 토큰(trusted approval event)은 로드맵(P1)입니다.
- **우발·부분 변조 감지** — 장부(SQLite)가 실수로 바뀌거나 일부 레코드가 깨지면 해시 체인 + Merkle 루트로 탐지합니다. 순서 뒤바뀜·꼬리 삭제·타임스탬프 역전도 잡습니다.
- **클라우드는 읽기·마스킹** — 클라우드 조회는 읽기 전용이며, 응답의 민감정보는 마스킹됩니다.

### 검증되는 불변식 (테스트로 강제)

사용자에게 하는 안전 약속은 회귀 테스트로 강제됩니다:

- 승인 전 활성 기억 수는 늘지 않는다 · 거절한 후보는 확정되지 않는다 — `binggu demo`, `tests/test_demo.py`
- MCP 로 preview→confirm 을 재현해도 저장/기각/교체가 차단된다(`G4_no_auto`) — `binggupack/mcp/server_handlers.py --selftest` (`*_preview_then_confirm_BLOCKED`)
- 키보드 `SAVE n` 앵커가 있을 때만 확정된다 — `scripts/smoke_test.py` (case 9b/9c)
- 교체된 기억은 기존 provenance(`replaced_by`/`supersedes`)를 잃지 않는다 — `openbinggu_candidate_replace_ux.py --selftest`
- 회상 결과는 사용된 기억의 식별자·근거를 제공한다 — `binggu recall`/`binggu explain`
- 무결성 검증 실패는 정상으로 표시하지 않는다(fail-closed) — `binggupack/pack/merkle_anchor.py`

### 범위 밖 (out of scope)

- **장부 파일에 직접 쓰기 권한을 가진 주체** — 그 PC의 소유자 본인이나 full shell 권한을 가진 프로세스가 장부와 해시 체인을 **통째로 재계산**하는 변조는 막지 않습니다. 로컬 개인 도구의 변조 감지는 **"우발·부분 손상 탐지"** 가 목적이며, 같은 머신 안의 전권 접근자를 상대로 한 암호학적 봉인은 위협 모델에 포함하지 않습니다. (같은 머신 안에 비밀키를 두는 방식은 그 키도 함께 읽히므로 실질 보호가 아니라 채택하지 않았습니다.)
- **하드닝된 다중 사용자 / 서버 배포** — 빙구팩은 팀 서버·멀티테넌트 환경용이 아닙니다.

## 데이터 보관

- 운영 정본은 **로컬**(`~/.binggupack`)입니다. 클라우드는 잠깐 거쳐 가는 보조 통로입니다.
- 시크릿·PII·운영키는 평문으로 로그·출력·메모리에 저장하지 않습니다.

## 지원 버전

최신 릴리스만 보안 수정을 받습니다. 현재 버전은 [releases/latest](https://github.com/darkjokee-arch/binggupack/releases/latest)를 참고하세요.
