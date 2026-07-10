# BingguPack — save-intent D4 4조건 게이트 검증표 (2026-06-12, 실측)

> **[P1-A 정합 노트]** confirm 을 "사람 발화 유래 증거"로 다루는 서술은 **NOT_A_TRUSTED_APPROVAL_CHANNEL**
> (전송된 confirm = 형식/무결성 검증일 뿐 사람 승인 아님). intent_id rehash 등 무결성 검증 결과는 유지.
> 사람 승인 = out-of-band trusted approval event(`docs/BINGGUPACK_TRUSTED_APPROVAL_EVENT_RFC.md`). 상세 = RFC §23.

> 설계 정본 `BINGGUPACK_HOSTED_SAVE_INTENT_DESIGN.md` §4·§5 D4 단계.
> 실측 도구: `scripts/openbinggu_save_intent_d4_e2e.py` — E2E(worker 적재 → pull → outbox → 러너 게이트 → temp DB).
> **결과 = 13/13 GATE=GO.** 전 DB temp 전용 · real staging 0 · live 0 · deploy 0 · 네트워크 127.0.0.1 한정.

## 검증표

| 조건 (설계 §4) | 케이스 | 실측 결과 |
|---|---|---|
| **1. 인증 상향** | C1-1 write 경로에 다른 키(read 키 유출 가정) | 404 — 도구 단위 분리, read 유출 시 write 불가 ✅ |
| | C1-2 브라우저 Origin | 403 (absent만 허용) ✅ |
| | C1-3 dev 재기동 후 구 키 | 404 — write 키 수명 = 프로세스(짧은 TTL) ✅ |
| **2. 전송 경로** | C2-1 worker 적재 완료 시점 로컬 DB | 노드 0 — worker DB write 0 ✅ |
| | C2-2 pull→outbox→러너 전 체인 | applied=1·노드 생성·intent 파일 소거 ✅ |
| | C2-3 변조 intent(위조 id, worker 모양검사 통과) | 러너 재해시 게이트가 reject(.rejected) — **게이트 본체는 러너** ✅ |
| **3. audit** | C3-1 적용 intent audit | `hosted_intent` ALLOW row 1 ✅ |
| | C3-2 DB 전체(노드·엣지·증거·audit) 원문 전문 검색 | 잔존 0 — 발췌(≤80자)/해시만 ✅ |
| | C3-3 audit chain | verify INTACT ✅ |
| **4. rollback/폐기** | C4-1 TTL 만료 intent | .expired 마킹만·미적용·DB 무변 ✅ |
| | C4-2 마킹 파일 | 원문(text) 미보관·text_sha 대체 ✅ |
| | C4-3 러너 재실행 | 자동 재시도 0 (.rejected/.expired 재처리 0) ✅ |
| | C4-4 적용 시 | snap_dir 스냅샷 생성 ✅ |

## 실측 중 발견 결함 → 수정

- 변조 intent를 동일 intent_id로 적재하면 worker store(Map)에서 **정상본이 덮임** → E2E는 위조 id 부여로 정정. 운영 함의: 동일 id 재적재 = 최신본 우선(멱등), 위변조는 어차피 러너 재해시에서 차단 — 설계 §1 위변조 방어 2겹 그대로.

## 단계 상태

- D1 설계 ✅ · D2 러너(16/16) ✅ · D3 canary non-retention(16/16) ✅ · **D4 검증표+실측(13/13) ✅**
- **D5 live 노출 = owner 명시 GO 의무 (미진행)** — deploy 0 유지
