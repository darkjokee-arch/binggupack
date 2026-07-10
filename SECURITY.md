# 보안 정책 (Security Policy)

## 취약점 신고

보안 문제를 발견하시면 **공개 이슈로 올리지 마시고** 아래 비공개 채널로 알려주세요:

- GitHub 비공개 신고: [Security Advisories](https://github.com/darkjokee-arch/binggupack/security/advisories/new)

가능하면 재현 방법과 영향 범위를 함께 주시면 빠르게 확인하겠습니다.

## 위협 모델 (무엇을 지키고, 무엇은 범위 밖인가)

빙구팩은 **개인이 자기 PC에서 쓰는 로컬 우선(local-first) 도구**입니다. 보안 경계는 이 전제 위에 설계됐습니다.

### 지키는 것 (in scope)

- **민감정보 유출 차단** — 비밀번호·인증서·PII·개인사(예: 이혼)는 애매하면 무조건 제외합니다(미리보기·회수·팩 업로드 전 구간에서 강제).
- **승인 없는 장부 확정 방지** — 내 기억 장부에 **확정(active) 저장**되는 것은 사람의 명시 승인을 거칩니다. 승인의 근거는 **모델이 재현할 수 없는 대역 외(out-of-band) 앵커**입니다: 내가 키보드로 친 `SAVE n` 을 hook 이 기록한 `save_gate_log`, CLI 의 TTY 입력, 또는 **trusted approval event**(P1-A). AI 가 preview 응답의 확인 문구(`SAVE 1` 등)를 그대로 **재현**해도 그것만으로는 사람 승인이 되지 않습니다. MCP 로 들어오는 저장/기각/교체(pair·deprecate·replace·mark·harvest)는 승인 앵커가 없으면 **fail-closed** 로 차단됩니다. (기억할 만한 발화가 **임시 후보(candidate)** 로 자동 수집되는 것과, 그 후보를 **장부에 확정**하는 것은 별개입니다 — 확정만 승인이 필요합니다.)
  - **Trusted approval event (P1-A · 구현됨).** MCP/모델은 승인 **요청**만 만들 수 있고, 승인 **발행**은 모델의 도구 표면 밖 owner 채널(`binggu approval approve`, 대화형 TTY)만 합니다. 승인은 **정확한 payload · operation · 대상 ledger · 프로토콜 버전**에 바인딩되고, **짧은 TTL · 1회용 consume** 입니다 — 재사용·재생(replay)·payload 한 글자 변경·다른 ledger·만료·거절/회수·동시 consume 이 전부 차단됩니다.
  - **한계(정직 · 배포 형태 의존).** 이 통제의 강도는 **모델의 도구 표면이 승인 저장 위치에 쓰지 못한다**가 성립하는 배포에서만 하드합니다(웹/앱 커넥터·잠긴 에이전트 — 모델에 파일/셸 도구가 없음). 모델에게 binggupack MCP **와 함께** 임의 파일/셸 도구(예: Filesystem/Bash MCP)를 준 호스트에서는 같은 에이전트가 승인 파일을 직접 만들 수 있으므로, 그 배포에서 P1-A 는 **자동저장 방지 + 승인 라우팅**이지 하드 보안 통제가 아닙니다 — 우리는 그 이상을 주장하지 않습니다. **어느 배포든 provider 미구성 시 fail-closed**(최악은 "안 써짐"이지 "몰래 써짐"이 아님)는 불변입니다. 승인 저장소를 도구 표면 밖(파일 루트 밖/OS 금고)에 두는 하드닝은 후속(P1-B)입니다.
- **우발·부분 변조 감지** — 장부(SQLite)가 실수로 바뀌거나 일부 레코드가 깨지면 해시 체인 + Merkle 루트로 탐지합니다. 순서 뒤바뀜·꼬리 삭제·타임스탬프 역전도 잡습니다.
- **클라우드는 읽기·마스킹** — 클라우드 조회는 읽기 전용이며, 응답의 민감정보는 마스킹됩니다.

### 검증되는 불변식 (테스트로 강제)

사용자에게 하는 안전 약속은 회귀 테스트로 강제됩니다:

- 승인 전 활성 기억 수는 늘지 않는다 · 거절한 후보는 확정되지 않는다 — `binggu demo`, `tests/test_demo.py`
- MCP 로 preview→confirm 을 재현해도 저장/기각/교체가 차단된다(fail-closed) — `binggupack/mcp/server_handlers.py --selftest` (`*_preview_then_confirm_BLOCKED`)
- trusted approval: owner 승인은 **정확히 1회만** 실행되고, 재생·payload 변경·다른 ledger·만료·거절/회수·동시 consume 은 전부 차단된다 — `scripts/openbinggu_trusted_approval_boundary_selftest.py`, `tests/test_trusted_approval_e2e.py`
- 승인 provider 미구성 시 모든 MCP mutation 은 fail-closed 이고 **운영 ledger mtime 는 불변**이다 — 위 하니스의 `no_provider_fail_closed` · `운영 ledger sentinel`
- `binggu approval approve` 는 **환경변수(BINGGU_TRUSTED_CLI 포함)·비대화형(pipe/redirect) 입력을 하드 거부**하고(no-write·exit≠0), 승인 발행은 대화형 TTY 에서만 한다 — `tests/test_trusted_approval_e2e.py`, `scripts/binggu_approval_origin_selftest.py`
- 승인 기원 계약(P1-A.1) — 두 층위로 정확히:
  - **trusted approval 이벤트 발행**(`approval approve`): 환경변수·stdin pipe·confirm 문구·bare isatty **단독**으로는 이벤트를 못 만든다 — 대화형 TTY **와** typed `APPROVE <rid8>` 문구가 모두 있어야 한다. (isatty 는 pipe/자동화를 거르는 UX 경계이지 암호학적 사람증명이 아니다 · 아래 "범위 밖" 참조.)
  - **CLI 운영 write**(save/pair/deprecate/replace/…): 환경변수·confirm 문구 단독은 사람 승인이 아니고, 비대화형(pipe)·앵커 없음이면 write 0(fail-closed). 사람 근거는 save_gate 앵커 **또는** 대화형 TTY 뿐이며, 이 중 대화형 TTY 는 UX 경계(비-암호학적)라 셸 병재 호스트에선 PTY 위조 가능 — 그 배포는 hard control 아님(정직 경계). `BINGGU_STRICT_HUMAN_GATE` 는 deprecated no-op(0/false 로 fail-open 불가).
  - production wheel 에 test 백도어(test_double 채널·환경변수 승인 read) 0. 검증: `scripts/binggu_approval_origin_selftest.py`(env/pipe/strict/save/pair/ship-guard/inventory) · `tests/test_trusted_approval_e2e.py`(PTY 대화형 성공경로·Unix). (MCP 표면의 client actor 무시 하드 오버라이드는 `binggupack/mcp/server_handlers.py --selftest`.)
- 키보드 `SAVE n` 앵커가 있을 때만 확정된다 — `scripts/smoke_test.py` (case 9b/9c)
- 교체된 기억은 기존 provenance(`replaced_by`/`supersedes`)를 잃지 않는다 — `openbinggu_candidate_replace_ux.py --selftest`
- 회상 결과는 사용된 기억의 식별자·근거를 제공한다 — `binggu recall`/`binggu explain`
- 무결성 검증 실패는 정상으로 표시하지 않는다(fail-closed) — `binggupack/pack/merkle_anchor.py`

### 범위 밖 (out of scope)

- **장부 파일에 직접 쓰기 권한을 가진 주체** — 그 PC의 소유자 본인이나 full shell 권한을 가진 프로세스가 장부와 해시 체인을 **통째로 재계산**하는 변조는 막지 않습니다. 로컬 개인 도구의 변조 감지는 **"우발·부분 손상 탐지"** 가 목적이며, 같은 머신 안의 전권 접근자를 상대로 한 암호학적 봉인은 위협 모델에 포함하지 않습니다. (같은 머신 안에 비밀키를 두는 방식은 그 키도 함께 읽히므로 실질 보호가 아니라 채택하지 않았습니다.)
- **모델에게 임의 파일/셸 도구를 함께 준 호스트에서의 trusted approval** — 그런 배포에서 approval 파일은 모델이 직접 쓸 수 있어 하드 통제가 아닙니다(위 "한계" 참조). 하드 통제는 모델 도구 표면이 approval 저장 위치에 쓰지 못하는 배포에서만 보장됩니다. (미구성 시 fail-closed 는 모든 배포 공통.)
- **하드닝된 다중 사용자 / 서버 배포** — 빙구팩은 팀 서버·멀티테넌트 환경용이 아닙니다.

## 데이터 보관

- 운영 정본은 **로컬**(`~/.binggupack`)입니다. 클라우드는 잠깐 거쳐 가는 보조 통로입니다.
- 시크릿·PII·운영키는 평문으로 로그·출력·메모리에 저장하지 않습니다.

## 지원 버전

최신 릴리스만 보안 수정을 받습니다. 현재 버전은 [releases/latest](https://github.com/darkjokee-arch/binggupack/releases/latest)를 참고하세요.
