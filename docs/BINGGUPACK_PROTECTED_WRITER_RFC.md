---
status: draft
name: BINGGUPACK_PROTECTED_WRITER_RFC
description: P1-B Track B — Protected Writer + Detached Signer. approval store 를 모델 tool surface 밖으로 옮겨 FS/Shell 병재 호스트에서도 승인 위조를 막는 하드닝 설계. Assurance Levels L0~L4 · Provider Matrix · 공격 프로토타입 명세. (설계 RFC · 미구현 · 정직 경계 계승)
metadata:
  type: reference
scope: project:binggupack
---

# BingguPack Protected Writer + Detached Signer — RFC (P1-B Track B · draft)

> "승인의 근거는 모델이 재현할 수 없는 대역 외(out-of-band) 앵커다." — SECURITY.md
>
> **정의.** *Protected writer* 는 approval store(승인 이벤트/서명키)를 **모델의 tool surface 가
> write 하지 못하는 신뢰 도메인**에 두는 배치다. *Detached signer* 는 그 도메인 안의 키로 승인
> 이벤트에 서명해, consume 측이 "모델이 만든 위조 레코드"와 "보호된 도메인이 발행한 진짜 레코드"를
> 구별하게 한다. **이 RFC 는 설계·위협모델·보증 등급만 정의한다 — 코드 변경 0, 구현은 후속.**

Base: `docs/p1b1-protected-writer-rfc` worktree (main `39d8c74` 후속). 상위 RFC =
`BINGGUPACK_TRUSTED_APPROVAL_EVENT_RFC.md`(P1-A · v2) — 본 문서는 그 **§26 R2/R5 를 채우는 설계
검토**다. 기존 문서 삭제 0 · 기존 코드 수정 0.

> **P1-B 트랙 분리.** Track A(`BINGGUPACK_P1B_MUTATION_CLOSURE_DESIGN.md`)는 mutation surface
> (accept/unaccept/due/resolve/confirm_edges·hosted 3파일·hag_sync_adapter)를 exact-bound approval
> 로 봉인한다 — **"승인이 없으면 안 써진다"**. Track B(본 문서)는 그 **후에 남는 위협** — **"모델이
> 승인 자체를 위조할 수 있다"** — 를 다룬다. 두 트랙은 직교하며 Track B 없이 Track A 는 FS/Shell
> 병재 호스트에서 하드 통제가 아니다(그 사실을 Track A 도 §1 에서 정직히 인정한다).

---

## 0. 요약 (한 문단)

P1-A/Track A 의 exact binding 은 **approval store 가 진실**이라는 전제 위에 선다. 그런데 owner 의 실제
dev 배포(binggupack MCP + Filesystem/Bash MCP 병재)에서 모델은 `~/.binggupack/{trusted_approval.json,
approvals.jsonl}` 를 **직접 write** 할 수 있다 → config 자가 활성화 + approve 레코드 위조 + 자가 consume
= 완전 우회. 현재 이 사실은 SECURITY.md §20·§44~45 와 P1-A RFC §4/§5 에 **정직히 문서화**돼 있으나
**하드 통제는 아니다**. Track B 는 이 out-of-scope 를 in-scope 로 끌어오는 5가지 배치(keychain·FS-root
밖·별도 프로세스·별도 디바이스·hosted)를 **보증 등급 L0~L4** 로 서열화하고, 각 등급이 **막는 공격 /
못 막는 공격 / 전제 / 비용**을 과장 없이 명시한다. **어떤 등급도 "이제 안전하다"를 주장하지 않는다** —
각 등급은 그 위 등급이 필요한 공격을 명시적으로 열거한다(P1-A 정직 경계 계승).

---

## 1. 문제 정의 — 왜 exact binding 이 장식이 되는가

### 1.1 현재(L1)의 신뢰 사슬

P1-A 의 consume 은 다음 사슬로 write 를 허가한다(실측 `trusted_approval.py`):

```
provider_for(home)  ← load_config(home) ← ~/.binggupack/trusted_approval.json  (평문 · 모델 write 가능)
verify_event(home, request_id, ...) ← read_events(home) ← ~/.binggupack/approvals.jsonl  (평문 append-only · 모델 write 가능)
reserve/finalize   ← approval_consumptions (DB · 모델 write 가능)
```

세 저장소 전부 `home`(=`~/.binggupack`) 아래 평문 파일/DB다. `verify_event` 는 **파일에 있는 레코드를
그대로 신뢰**한다 — 서명 검증도, 발행자 인증도 없다. `approver_channel` 필드는 감사 메타일 뿐
`verify_event` 가 검증하지 않는다(모듈 docstring 명시).

### 1.2 위조 절차 (3-step, 코드 실측)

FS/Shell 도구를 가진 같은 에이전트는:

1. **config 자가 활성화** — `trusted_approval.json` 에 `{"enabled": true}` write → `provider_for` 가
   `LocalOwnerProvider` 반환(원래 owner 만 만들 파일).
2. **approve 레코드 위조** — `approvals.jsonl` 에 `{request_id, ..., approval_nonce:<자가생성>,
   approved_at:now, expires_at:now+900, record_type:"approve"}` 한 줄 append(boundary selftest
   §5 line 164-167 이 정확히 이 append 를 정당 테스트로 사용 — 즉 위조도 문법적으로 동일).
3. **자가 consume** — MCP 핸들러에 `approval_id=request_id` 제시 → `verify_event` ok → reserve/
   finalize → `executed_write=True`. **owner 는 관여한 적 없다.**

### 1.3 "같은 머신 키 = 보안 연극"을 우회하는 문제

P1-A §6 은 서명 토큰을 **의도적으로 거부**했다: 서명키를 같은 `home` 에 두면 모델이 그 키도 읽어
유효 서명을 위조하므로 "보안 연극"(SECURITY.md §44). **이 판단은 옳다** — 단, 그 결론은 "키를 같은
신뢰 도메인에 둘 때만" 성립한다. Track B 의 핵심 질문:

> **키/store 를 모델이 읽지도 쓰지도 못하는 다른 신뢰 도메인에 둘 수 있는가? 있다면 서명은 연극이
> 아니다.** 그 도메인이 (a) OS keychain, (b) 다른 사용자/프로세스, (c) 별도 디바이스다.

각 후보의 실현성·전제·한계가 §2~§4다. **결론 선점(정직):** owner 의 FS+Shell 병재 dev 머신에서
**셸이 있는 한 (b)의 별도-사용자·(a)의 일부는 무력**하다 — 진짜 하드는 (c) 별도 디바이스뿐이고, 나머지는
"공격 비용을 올리는 부분 하드닝"이다. 이 서열을 §5 등급표가 못박는다.

---

## 2. Protected Writer — approval store 를 tool surface 밖으로

승인 레코드(및 서명키)를 모델 도구가 접근 못 하는 곳에 두는 4가지 배치. 각각 **실현성·전제·무력화
조건**을 명시한다.

### 2.1 (a) OS keychain / credential manager

| 항목 | 내용 |
|---|---|
| 배치 | approval EVENT 레코드(또는 그것을 서명하는 키)를 **OS 보호 저장소**에: Windows **DPAPI**/Credential Manager · macOS **Keychain** · Linux **secret-service(libsecret)/kernel keyring**. |
| 접근 통제 | keychain read/write 는 **로그인 사용자 컨텍스트 + (플랫폼별) 프로세스 ACL**로 통제. `approvals.jsonl` 평문 대신 keychain 항목에 승인 nonce/서명키를 저장. |
| 막는 것 | 모델이 **FS 도구로 `~/.binggupack` 를 뒤져도** keychain 항목은 파일이 아니라 못 읽는다. 평문 위조 append 불가(store 가 파일이 아님). |
| **못 막는 것(핵심)** | 모델이 **셸/코드 실행**을 가지면 같은 로그인 사용자로 `keyctl`/`security`/DPAPI API 를 호출해 항목을 읽거나 새로 쓸 수 있다 — keychain 은 **사용자 단위** 보호이지 **프로세스 단위**가 아니다(플랫폼 대부분). 즉 **L2 = FS-only 공격 차단, 코드실행 공격 미차단**. |
| 전제 | ① 모델 도구에 임의 셸/네이티브 코드 실행이 없음(FS 도구만) — 이게 깨지면 무력. ② 플랫폼 keychain API 가용·이식 코드. |
| 비용 | 중. 플랫폼 3종 분기(win32/darwin/linux) + fallback(keychain 부재 시 L0 fail-closed). `keyring` 파이썬 패키지 의존 or 플랫폼 native 호출. |

### 2.2 (b) FS-root 밖 경로 + OS 권한

| 항목 | 내용 |
|---|---|
| 배치 | approval store 를 `~/.binggupack`(모델 FS 도구의 allow-root) **밖**의 디렉토리로 이동 + OS 파일 권한(예: `0700`, 소유자만)으로 잠금. 모델 FS MCP 의 allow-list 에 그 경로가 없으면 접근 거부. |
| 막는 것 | 모델 FS MCP 가 **allow-root 로 제한**된 경우(예: `filesystem` MCP 를 `~/.binggupack` 나 프로젝트로 한정) → store 경로가 밖이면 read/write 거부. `physical_store_isolation.py` 가 이미 user_root namespace 밖 접근을 `outside_user_namespace` 로 BLOCK 하는 판정 로직(prior art)을 갖고 있다. |
| **못 막는 것(핵심)** | ① 모델에 **셸**이 있으면 allow-root 무관하게 임의 경로 접근 → **무력**. ② FS MCP 가 allow-root 없이 홈 전체/광범위 허용이면 무력. ③ OS 권한도 같은 사용자면 셸로 우회. |
| 전제 | 모델 도구가 **경로-제한 FS 만** 갖고 셸이 없음. owner dev 머신(Shell MCP 병재)에서는 **전제 불성립 → 무력**. |
| 비용 | 저. 경로 상수 + `os.chmod` + `_same_path`(기존) 확장. **단 보호 강도 낮음** — 셸 한 줄로 뚫림. "정직 라벨: 이건 하드닝이 아니라 실수 방지 수준." |

> **(b) 정직 판정:** owner 실환경엔 사실상 무의미(셸 병재). 웹/앱 커넥터(경로-제한 or FS 없음)엔
> 이미 L1 이 하드하므로 (b)의 추가 이득도 작다. → **채택 우선순위 최하**. 문서엔 남기되 "권장 아님".

### 2.3 (c) 별도 프로세스 / 다른 사용자 / 컨테이너가 store 소유

| 항목 | 내용 |
|---|---|
| 배치 | **privileged helper 프로세스**(별도 OS 사용자 `binggu-signer`, 또는 컨테이너/VM)가 approval store 를 **소유**한다. 모델·MCP 서버는 store 를 직접 못 만지고, **IPC**(local socket·named pipe·gRPC)로 "이 request 를 owner 가 승인했는지 확인/발행" 요청만 보낸다. 서명키는 helper 안에만. |
| 막는 것 | 모델이 FS/셸을 **자기 사용자로** 가져도 `binggu-signer` 사용자 소유 파일/소켓 권한(`0600`, 다른 uid)엔 접근 불가 → 위조 append·키 read 불가. helper 는 승인 발행 전 **out-of-band owner 확인**(자기 TTY·별도 인증)을 요구 → 모델 IPC 만으론 발행 안 됨. |
| **못 막는 것(핵심)** | ① 모델이 **다른 사용자 권한 상승(sudo/권한 취약점)**을 얻으면 무력 — 그러나 이는 "arbitrary local root" 로 SECURITY.md §44 범위 밖(모든 로컬 도구 공통 한계). ② helper 가 owner 확인을 **같은 화면 프롬프트**로만 하면 모델이 그 프롬프트를 자동 응답할 수 있어(같은 세션) 약화 → owner 확인이 **별도 신뢰 경로**(디바이스/생체/물리 키)여야 강함. ③ IPC 표면 자체의 취약점(injection). |
| 전제 | ① OS 다중 사용자·권한 분리 가능(Windows service account·Linux 별도 uid·컨테이너). ② 모델이 그 사용자로 권한 상승 못 함. owner dev 머신에서 **셸은 있으나 다른 uid 로의 상승은 없다**면 → **이 등급이 owner 환경 첫 실질 하드닝**. |
| 비용 | 고. helper 데몬 + IPC 프로토콜 + 설치(서비스 등록·별도 사용자 생성·권한) + 크로스플랫폼. UX: owner 가 helper 설치·승인 흐름 학습. |

### 2.4 (d) 별도 디바이스 (폰 앱 / 하드웨어 토큰)

| 항목 | 내용 |
|---|---|
| 배치 | 승인이 **완전히 다른 신뢰 도메인**에서 발행된다: owner 폰 앱이 request digest 를 보고 **폰 안의 키**로 서명 → 서명된 승인이 PC 로 전달 → consume 은 폰 공개키로 검증. 또는 하드웨어 토큰(FIDO2/YubiKey·시크릿 폰) challenge-response. |
| 막는 것 | PC 의 모델이 FS·셸·심지어 **root** 를 가져도 **폰의 개인키가 PC 에 없으므로** 유효 서명 위조 불가. request digest 를 owner 가 폰에서 **눈으로 확인** 후 승인 → owner-saw==committed 가 다른 도메인에서 성립. **PC 전권 접근자조차 못 뚫는 유일한 등급.** |
| **못 막는 것(정직)** | ① 폰-PC **전달 채널 변조**(모델이 다른 request digest 를 폰에 보내 owner 를 속임) → 폰 화면에 **사람이 읽을 요약**을 띄워 완화하나 owner 가 대충 승인하면 무력(사회공학). ② 폰 자체 탈취·멀웨어. ③ 서명은 "이 digest 를 승인"만 증명 — payload↔digest 렌더가 PC 측이면 렌더 위조 여지(폰에서 최종 문장까지 렌더해야 완결). ④ owner UX 마찰(매 저장마다 폰). |
| 전제 | 폰/토큰이 PC 와 **독립 신뢰 도메인**(같은 멀웨어에 동시 감염 안 됨). 전달 채널은 신뢰 불요(서명이 무결성 보장)하나 **표시 무결성**은 폰이 책임. |
| 비용 | 최고. 폰 앱(iOS/Android)·페어링·키 관리·전달 프로토콜·오프라인 대비. hosted provider(§4 R5)와 결합 가능. |

---

## 3. Detached Signer — 서명이 "연극"이 아닌 조건

### 3.1 핵심 원리

승인 EVENT 에 서명 `sig = Sign(sk, canonical_record)` 를 붙이고, consume 이 `Verify(pk, record, sig)` 로
검증한다. **연극 여부는 오직 `sk`(서명키)가 어디 있느냐로 갈린다:**

```
sk 가 모델 tool surface 안(같은 home 평문)      → 모델이 sk read → 유효 sig 위조 → 연극 (P1-A §6 이 거부한 것)
sk 가 OS keychain(사용자 보호)                  → FS 도구는 못 읽음 · 셸/코드실행은 읽음 → L2 (부분)
sk 가 별도 프로세스/사용자 소유                  → 같은 uid 모델은 못 읽음 · 권한상승은 읽음 → L3
sk 가 별도 디바이스(폰/토큰)                     → PC 전권자도 못 읽음 → L4 (연극 아님 · 완전)
```

즉 **P1-A §6 의 "같은 머신 키 = 연극"은 참이지만 조건부**다("같은 **신뢰 도메인**"일 때). Detached
signer = 키를 다른 도메인으로 빼는 것. 서명 알고리즘 자체(HMAC vs Ed25519)는 부차적 — **키 격리가 본질**.

### 3.2 서명 방식별 위협 모델

| 방식 | 키 종류 | 검증자 | 적합 등급 | 비고 |
|---|---|---|---|---|
| **HMAC-SHA256** | 대칭(sign=verify 키 동일) | 같은 키 보유자 | L2/L3 | consume 도 키가 있어야 검증 → 검증자(MCP consume)도 신뢰 도메인 안이어야. **모델 consume 이 같은 프로세스면 키 노출** → HMAC 은 **helper 가 검증까지 수행**(L3)해야 안전. 대칭키를 consume 측에 주면 L2 에서도 위조 가능 → **부적합 다수**. |
| **Ed25519 등 비대칭** | 서명키(sk)=보호 도메인 / 검증키(pk)=공개 | 누구나(pk 로) | L2/L3/L4 | consume 은 **공개키만** 필요 → 검증을 MCP 측에서 해도 sk 미노출. **detached signer 의 정석.** 폰/토큰(L4)과 자연 결합(FIDO2·Ed25519). |
| **기존 `sign_util.py` HMAC** | 대칭(save-intent transport 인증) | 서버 | — | **재사용 금지.** 이건 **transport 인증**(hosted→PC 전달 무결성)이지 approval-anchor 서명이 아니다. 같은 `sign_material` 이 owner 머신에 있어 approval 서명에 쓰면 정확히 §6 연극. Track B 는 **별도 키/별도 도메인**을 요구. |

> **정직:** HMAC 대칭키는 "검증자도 위조 가능"이라 approval-anchor 로는 **비대칭(Ed25519)이 원칙**.
> L3 에서 helper 가 sign+verify 를 **둘 다** 소유하면 HMAC 도 성립하나, MCP consume 이 helper IPC 로
> 검증을 위임해야 한다(consume 프로세스에 키를 주는 순간 무너짐).

### 3.3 서명이 바인딩해야 하는 것

기존 `verify_event` 의 바인딩(operation·payload_digest·ledger_id·protocol·TTL·nonce)을 **서명이 덮어야**
한다. 즉 서명 대상 = `canonical(record without sig)` 전체. 그래야 서명 후 필드 한 개 변조 시 검증 실패.
`payload_digest` 는 P1-A §9 canonical digest 를 그대로 재사용(렌더러=실행기 동일 explicit 원칙 계승).

---

## 4. Assurance Levels L0~L4

각 등급: **막는 공격 / 못 막는 공격(그 위 등급 필요) / 전제 / 구현 비용.** 과장 0.

| Lv | store/키 위치 | 막는 공격 | **못 막는 공격(정직)** | 전제 | 비용 |
|---|---|---|---|---|---|
| **L0** | provider 미구성 | **모든 write**(fail-closed). 최악="안 써짐". | (없음 — 기능도 0) | 없음 | 0 (현행) |
| **L1** | 평문 `~/.binggupack`(현행 P1-A) | 자동저장 · dry-run 재현 · env/actor/confirm spoof · replay · payload/ledger/protocol 변조 · 만료/거절. **MCP-only 표면**(웹/앱 커넥터·잠긴 에이전트)에선 **하드**. | **FS/Shell 병재 호스트의 store 직접 위조**(§1.2 3-step). owner dev 머신 = 여기서 뚫림. | 모델 도구에 approval store write 수단 없음(경로-제한 or FS 없음) | 0 (구현됨) |
| **L2** | OS keychain(사용자 보호) | L1 전부 + **FS-only 위조**(모델이 파일 뒤져도 keychain 못 읽음·평문 append 불가). | **셸/네이티브 코드 실행 공격**(같은 사용자로 keychain API 호출) · 권한상승. | 모델 도구=FS만·셸 없음 · keychain API 가용 | 중(플랫폼 3분기+fallback) |
| **L3** | 별도 프로세스/사용자 소유 + IPC + 비대칭 서명 | L2 전부 + **같은 uid 셸/코드실행 위조**(다른 uid store·키 미접근). helper 가 out-of-band owner 확인 강제. | **권한상승(sudo/취약점)** = arbitrary local root(§44 공통 한계) · helper 의 owner 확인이 같은 화면이면 자동응답 · IPC injection. | OS 사용자 분리 가능 · 모델이 권한상승 못 함 · owner 확인이 별도 경로면 강 | 고(데몬·IPC·설치·서비스 계정) |
| **L4** | 별도 디바이스(폰/하드웨어 토큰)·비대칭 sk 는 디바이스 안 | L3 전부 + **PC 전권(root 포함) 위조**(개인키가 PC 에 없음). owner 가 폰에서 digest 확인 → 다른 도메인 owner-saw==committed. | **전달 채널 표시 위조 사회공학**(owner 가 대충 승인) · 디바이스 탈취/멀웨어 · payload↔digest 렌더 무결성(폰이 최종 렌더해야). | 폰/토큰=독립 신뢰 도메인 · 표시 무결성은 디바이스 책임 | 최고(모바일 앱·페어링·키관리·hosted 결합) |

**등급 간 단조성.** L(n) 은 L(n-1) 이 막는 것을 전부 막고 **새 공격 계층 하나**를 추가로 막는다.
어떤 등급도 "완전 안전"이 아니다 — 각 행의 "못 막는 공격"이 그 증거다. **owner 는 자기 위협모델에서
어느 계층까지 막을지 선택**한다(§7 권고).

**핵심 경계선(owner dev 머신).** owner 실환경은 **셸 병재** → **L2 도 코드실행으로 뚫린다**. owner
환경에서 실질 하드의 **최소선은 L3**(별도 사용자/프로세스), 완전은 **L4**(별도 디바이스). L1/L2 는 owner
dev 머신에선 "자동저장 방지 + 공격 비용 상승"이지 하드 통제가 아니다 — 이 문장을 SECURITY.md 에 유지.

---

## 5. Provider Matrix

`trusted_approval.py` 의 `provider_for`/`LocalOwnerProvider`/`HostedProvider(NotImplementedError)` 를
어떻게 확장하는가. **현재 구현 상태를 정직히 표기**(대부분 미구현).

| provider | store 위치 | 서명 | assurance | 현재 상태 | placeholder 를 채우는 법 |
|---|---|---|---|---|---|
| `local_owner`(현행) | `~/.binggupack/approvals.jsonl` 평문 | 없음 | **L1** | **구현됨**(P1-A) | — (MCP-only 배포에서만 하드) |
| `keychain` | OS keychain 항목 | HMAC/Ed25519 키를 keychain 에 | **L2** | 미구현 | 신규 `KeychainProvider(kind="keychain")`: `store/read_events` 를 keychain read/write 로 오버라이드. `verify_event` 는 서명 검증 추가. fallback: keychain 부재→L0. |
| `detached_process` | 별도 uid/컨테이너 소유 파일 + local socket | Ed25519(sk=helper 안) | **L3** | 미구현 | 신규 `DetachedProcessProvider`: `mint`/`verify` 를 helper IPC 호출로. helper 데몬 별도 배포. MCP 는 pk 로 검증만. |
| `detached_device` | 폰 앱/하드웨어 토큰 | Ed25519/FIDO2(sk=디바이스) | **L4** | 미구현 | 신규 `DetachedDeviceProvider`: request digest 를 디바이스로 push → 서명 회수 → pk 검증. hosted(§R5) 전달 채널 재사용 가능. |
| `hosted`(현행 stub) | (전달만·권한 아님) | transport HMAC(`sign_util`) | — | `NotImplementedError`(P1-B) | **주의**: hosted 는 **transport**(전달)이지 approval **발행**이 아니다. hosted+detached_device 결합 시에만 L4 권한. hosted 단독은 P1-A §23 대로 **untrusted intent**. |

**공통 계약(모든 provider).** `provider_for(home) is None → fail-closed`(L0) 불변. 서명 provider 는
`verify_event` 에 **서명 검증 단계 추가**(pk 로) — 서명 없거나 invalid → `binding_mismatch:signature`
→ fail-closed. 이로써 L1 평문 위조 레코드는 L2+ verify 에서 **자동 거부**된다(§6 데모가 실증).

**인터페이스 스케치(설계만·미구현):**

```python
class SigningProvider:              # L2~L4 공통 추상
    kind: str
    def public_key(self) -> bytes: ...          # consume 측 검증용(sk 미노출)
    def mint_signed(self, request, ttl, now) -> dict: ...   # 보호 도메인 안에서만 서명
    def verify_signed(self, record) -> bool: ...            # pk 로 검증(어디서나 안전)
# provider_for 는 config 의 kind 로 분기. 미구현 kind → NotImplementedError(L0 fail-closed 로 낙하 아님 —
# owner 가 명시 구성한 kind 가 미구현이면 "구성됐으나 못 씀" = fail-closed 가 맞다).
```

---

## 6. 공격 프로토타입 명세 (defensive)

### 6.1 목적

**FS 병재 호스트에서 L1 승인 위조가 성립함**을 temp 격리 home 에서 재현(운영 `~/.binggupack` 미접촉)하고,
**protected writer(별도 도메인 서명 store 시뮬)가 그 위조를 차단**함을 대비 실증한다. P1-A 의 boundary
selftest 가 정당 테스트로 쓰는 `append_event` 직접 호출이 곧 위조 벡터라는 점을 드러낸다.

### 6.2 의사코드 (핵심 흐름)

```
# --- 준비: temp home · 운영 홈 sentinel(mtime before==after) ---
home = mkdtemp(); assert real ~/.binggupack 미접촉

# --- ATTACK L1 (현행 · 위조 성립) ---
req    = build_request(op="deprecate", payload=..., ledger_id=uuid)   # 모델이 MCP 로 만든 PENDING
forge  = {**req, approval_nonce: rand, approved_at: now, expires_at: now+900, record_type:"approve"}
append_event(home, forge)                    # ← FS/Bash MCP 가 하는 것(owner TTY 0)
assert verify_event(home, ...).ok == True    # BYPASS: consume 이 위조를 진짜로 신뢰

# --- DEFENSE L2+ (protected writer 시뮬 · 서명 store) ---
vault_sk, vault_pk = keygen()                # vault = 모델 tool surface 밖(keychain/별도 프로세스 표현)
signed_append(protected_store, sign(vault_sk, req))     # owner 도메인만 서명
# 공격자 재현: vault 밖에서 위조 시도
forge2 = {**req, approval_nonce: rand, record_type:"approve"}      # 서명 없음(sk 못 읽음)
append_plaintext(protected_store, forge2)    # 파일엔 쓸 수 있어도…
assert verify_signed(vault_pk, forge2) == False   # DEFENSE: 서명 없어 거부 → fail-closed
assert verify_signed(vault_pk, signed_owner_record) == True

# --- 정직 캡션(반드시 출력) ---
print("가정: vault sk 가 모델 도구로 read 불가(keychain L2/별도 프로세스 L3/디바이스 L4).")
print("이 가정이 깨지면(모델이 sk read) L2 는 연극으로 환원 = §6 P1-A 판단과 동일.")
```

### 6.3 각 등급이 위조를 차단하는 지점

| 공격 단계 | L1 | L2(keychain) | L3(별도 프로세스) | L4(디바이스) |
|---|---|---|---|---|
| config 자가 활성화 | 성립 | store 가 keychain→config 파일 무의미 | helper 가 소유→불가 | 디바이스 페어링 필요 |
| approve 위조 append | **성립(우회)** | 평문 append 무시(서명 검증)·단 코드실행 시 keychain write 가능 | 다른 uid→append 불가 | sk 없어 서명 불가 |
| 자가 consume | 성립 | 서명 없으면 거부 | 서명·발행 모두 helper | pk 검증 실패 |
| **최종 차단 계층** | (없음) | FS-only 공격 | 같은 uid 코드실행 | PC 전권 |

### 6.4 산출물

- 의사코드/명세: **본 문서 §6**.
- 실행 스켈레톤: `scripts/binggu_protected_writer_attack_demo.py` — 헤더에
  `"defensive security · attack-model demonstration · P1-B Track B · 운영 store 미접촉"` 명시.
  temp 격리 home·운영 홈 sentinel·L1 위조 성립 + L2+ 서명 store 차단 대비 selftest. 실행:
  `python scripts/binggu_protected_writer_attack_demo.py --selftest`.

---

## 7. 권고 — owner 실환경(FS/Shell 병재)의 현실적 최소 하드닝

### 7.1 배포 구분

| 배포 | 현재 하드니스 | 권고 |
|---|---|---|
| **웹/앱 커넥터**(MCP-only·경로제한·셸 없음) | **L1 이 이미 하드** | 추가 불요. L1 유지 + provider 미구성 fail-closed. hosted+device 결합 시 L4. |
| **owner dev 머신**(binggupack MCP + FS/Bash MCP) | **하드 아님**(L1 뚫림) | **최소 L3**(별도 프로세스/사용자 소유 store) 목표. 중간 단계로 L2(keychain)는 "FS-only 공격은 막되 셸은 못 막음"을 **정직 라벨**과 함께. **L4 가 유일한 완전**. |

### 7.2 단계적 채택 순서 (비용 대비 이득)

1. **L0/L1 정직 문서화 강화(이미 됨·비용 0).** owner dev 머신에서 하드 아님을 SECURITY.md·README 에
   prominent 유지. **이것이 최우선** — 사용자가 하드로 오인하지 않게.
2. **L2(keychain) — 중간 방어(비용 중).** FS-only 위조를 막아 "실수·저권한 도구" 방어. **단 셸 병재
   owner 환경에선 하드 아님**을 라벨. 웹/앱 배포엔 이득 적음(이미 L1 하드).
3. **L3(별도 프로세스) — owner 환경 첫 실질 하드(비용 고).** owner 가 진짜로 FS/Shell 병재에서
   승인 위조를 막고 싶으면 여기부터. helper 데몬 + 별도 uid.
4. **L4(별도 디바이스) — 완전(비용 최고).** 고보증 요구 시. hosted provider(R5)와 결합.

**정직 권고 요지:** owner 의 실제 위협("모델이 내 승인을 위조")을 **진짜로** 막는 최소선은 **L3**,
완전은 **L4**. L1/L2 는 owner dev 머신에선 보조 방어일 뿐이다. **"L2 넣었으니 안전"이라 말하지 않는다.**

---

## 8. Unresolved / 비용 / 이식성

- **R-B1. keychain 이식성.** win32(DPAPI/CredMan)·darwin(Keychain)·linux(secret-service/keyring) 3분기 +
  headless/CI fallback. `keyring` 패키지 의존 도입 여부(경량 vs 순수 stdlib native 호출). 일부 Linux(WSL·
  헤드리스)엔 secret-service 부재 → L0 fallback 필수.
- **R-B2. L2 의 근본 한계.** keychain 은 **사용자 단위** 보호 → 같은 사용자 셸/코드실행이면 무력. owner
  dev 머신(셸 병재)에서 L2 는 "FS-only 공격 차단"만 — 이 한계를 문서가 과장하면 정직 위반. L2 를 "하드"로
  홍보 금지.
- **R-B3. L3 helper 의 owner 확인 경로.** helper 가 승인 발행 전 owner 확인을 **같은 화면 프롬프트**로만
  하면 같은 세션 모델이 자동응답 가능 → owner 확인이 **별도 신뢰 경로**(디바이스·물리 키·별도 로그인)여야
  강함. 아니면 L3 이 L2 수준으로 약화. 이 설계 상세가 미결.
- **R-B4. L4 표시 무결성.** 폰이 승인하는 것은 digest — payload↔문장 렌더가 PC 측이면 렌더 위조 여지.
  폰에서 **최종 저장 문장까지** 렌더해야 owner-saw==committed 완결. 전달 프로토콜·오프라인·페어링 UX 미결.
- **R-B5. 비대칭 vs 대칭.** detached signer 는 **Ed25519(비대칭)** 원칙(검증자에 키 불요). HMAC 대칭은
  L3 helper 가 sign+verify 둘 다 소유할 때만. 라이브러리 의존(PyNaCl vs stdlib `hashlib`+순수 Ed25519).
- **R-B6. consume 검증 위치.** L3/L4 에서 verify 를 MCP consume 프로세스가 pk 로 하면 안전(sk 미노출).
  단 consume 로직이 provider 를 신뢰하는 접점(`provider_for`)이 여전히 config 파일 신호 → **config 자체를
  보호 도메인에 두거나** provider 발견을 서명된 매니페스트로 바꿔야 완결(config 위조로 provider 강등 방지).
- **R-B7. UX 마찰 대 보증.** L3/L4 는 매 저장에 out-of-band 확인 → owner 피로. 배치(batch) 승인·세션
  단위 승인 창은 보증을 낮춤 → 마찰↔보증 트레이드오프 정책 미결.
- **R-B8. Track A 와의 결합.** Track A 가 mutation surface 를 exact-bound approval 로 봉인 → Track B 는
  그 approval store 를 하드닝. 두 트랙 병합 시 provider 인터페이스가 `binding_fields`(Track A 확장)와
  서명(Track B)을 **동시** 만족해야 — 서명 대상 = Track A 의 확장된 canonical digest. 순서: Track A 먼저
  merge(binding), Track B 가 그 위에 서명 계층. 동시 구현 시 인터페이스 충돌 주의.

---

## 부록 A. 기존 자산 재사용/구분

| 기존 파일 | Track B 관계 |
|---|---|
| `binggupack/safety/trusted_approval.py` | `provider_for`/`verify_event`/`mint_approval` 확장 지점. 서명 검증 단계 추가(§5). **수정은 후속 구현 — 본 RFC 는 설계만**. |
| `binggupack/safety/sign_util.py` | **transport HMAC**(hosted 전달 인증). approval-anchor 서명과 **별개** — 재사용 금지(§3.2). 같은 머신 키라 approval 에 쓰면 §6 연극. |
| `binggupack/safety/physical_store_isolation.py` | path namespace 격리 판정(prior art·L(b) 참고). dry-run validator·OS 권한 강제는 아님. L2/L3 store 경로 격리 설계 시 참조. |
| `binggupack/safety/path_safety.py`, `_same_path`(§10 P1-A) | store 위치 경로 검증(symlink/case-alias). L(b) FS-root 밖 경로 검사에 재사용. |

## 부록 B. 이 RFC 가 주장하지 않는 것 (정직 경계 명시)

- "Track B 를 구현하면 안전하다" — **아니다.** 각 등급은 위 등급이 필요한 공격을 열거한다(§4).
- "L2(keychain)가 owner dev 머신을 하드하게 만든다" — **아니다.** 셸 병재면 L2 는 FS-only 만 막는다.
- "서명을 넣으면 §6 연극이 해소된다" — **조건부다.** 키가 다른 신뢰 도메인에 있을 때만(§3.1).
- "이 문서가 코드를 바꾼다" — **아니다.** 설계·위협모델·등급표만. 구현은 후속 PR.
