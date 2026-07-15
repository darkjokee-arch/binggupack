# Hosted Relay Profile — Memory PR Spec v0.1-draft

> **상태: v0.1-draft (프로젝트 초안)**
> BingguPack 단일 프로젝트의 내부 재현 문서 초안이다. 독립 구현·표준 단체·vendor-neutral 채택 주장 아님. 코드/CLI/DB 변경 0(정적 문서화). 값은 2026-07-15 코드 실측 기준이며, spec 확정·구현·push 는 재4CLI blocker 0 + owner GO 후.
> **doc-profile: `hosted-relay`** — 이 라벨은 문서용 네임스페이스일 뿐, **어떤 해시 재료에도 들어가지 않는다.** wire version 은 `schema_ver=1`(intent payload). 재현자는 실제 코드 재료 문자열을 그대로 사용한다.

---

## 0. 개요 — 원격 intent 전달 프로필

Hosted Relay 는 **원격 채널(ChatGPT / 웹 / 모바일)에서 발생한 저장 의사(save intent)를 로컬로 전달**하는 프로필이다. **별도 저장 프로토콜이 아니다.**

```
ChatGPT/웹/모바일  ──save intent──▶  hosted worker  ──local pull──▶  로컬 preview + 사람 승인
                                                                          │
                                                            ▼ 최종 저장은 Interactive Save 로 수렴
                                                    commit_bundle (actor=human · SAVE n)
```

- **최종 저장은 Interactive Save 의 `commit_bundle` 게이트로 수렴한다.** Hosted 는 원격에서 온 intent 를 로컬 스테이징까지 나르는 **전달(relay)** 역할만 하고, 실제 write 는 로컬 사람 승인 뒤 Interactive Save 와 **동일한 산식·동일한 게이트**를 재사용한다. Hosted 고유의 저장 산식은 없다.
- 흐름: 원격 save intent → hosted worker(무상태 relay) → 로컬 pull → 로컬 preview → 사람 승인(SAVE n) → commit.
- 공통 불변식(전 프로필 공유): ① preview/intent 만으로는 저장 0 ② 저장 노드 `candidate=1`·`promotion_allowed=0`(정본 미승격) ③ fail-closed 기본 `actor=reader` ④ 사람 승인만 write.

---

## 1. 신뢰 주체

| 주체 | 역할 | 권한 |
|---|---|---|
| hosted worker | **relay 전용** | 장부(ledger) 0 · 파일시스템(FS) 0 |
| 로컬 `ledger.sqlite` | **정본**(BOUNDARY L3) | 유일한 write 대상 |
| 로컬 사람 SAVE n | **최종 승인** | `commit_bundle` actor=='human' + confirm=='SAVE <idx>' 만 write |

- hosted worker 는 intent 를 받아 잠깐 보관·전달만 한다. 장부에 쓰지 않고 파일시스템도 없다.
- **정본은 로컬 `ledger.sqlite` 하나뿐**이다.
- 최종 승인은 **로컬 사람의 SAVE n** 이다. `commit_bundle` 은 `actor=='human'` 이고 confirm 이 `"SAVE <idx>"` 일 때만 write 한다. **전달되어 온(transported) actor / confirm 값은 신뢰하지 않는다**(계약 11). 원격이 "이미 승인됨"이라고 주장해도 로컬 게이트가 사람 승인을 재요구한다.

---

## 2. ★ hosted ≠ local writer — 코드 증명 5가지

hosted worker 가 로컬 장부에 절대 쓰지 못한다는 것을 코드로 증명한다.

1. **Worker 에 `ledger.sqlite` 접근 코드 자체가 부재** — Worker 코드에는 `storage.put/delete/list` 만 있고 로컬 장부를 여는 경로가 없다.
2. **응답 text echo 0** — Worker 응답은 `intent_id` 만 돌려주고 저장될 본문(text)을 되울리지 않는다.
3. **pull 러너 actor=reader 하드코딩** — 로컬 pull 러너의 컨텍스트는 `actor=reader` 로 하드코딩되어 `human_save_required` 로 귀결 → pull 단계 자체의 write 0.
4. **레거시 `process_outbox` 게이트 항상 BLOCK** — 옛 자동 저장 경로(process_outbox)는 게이트 5에서 항상 `direct_write_disabled` 로 BLOCK.
5. **selftest 불변식** — selftest 에서 `op_before == op_after` · `nodes == 0` · `approval_requests == 0`. relay 를 태워도 장부 노드 수가 늘지 않는다.

---

## 3. 코드 경로 (5단계)

| 단계 | 심볼 / 엔드포인트 | 동작 | write |
|---|---|---|---|
| ① 원격 적재 | `save_intent_mcp.ts` POST `/mcp2/<키>` | Origin 가드 + 경로키(HMAC 없음) → `intentHash` → PII 백스톱 → stub `/put` | 원격 DO |
| ② hosted 수신 | `save_intent_v2.ts` `IntentInbox` DO | `storage` 키 `intent:<id>`(R2/KV 아님) · 만료 alarm purge(delete) | DO storage |
| ③ 로컬 pull | `binggu.py cmd_hosted` → `live_runner`(HMAC signed) → `/save2/<token>/pull` → DO `/drain`(read+delete atomic·non-retention) → `hosted_inbox.fetch_to_staging` | `home/hosted_inbox/<id>.json` (ledger write 0) | 로컬 staging |
| ④ preview | `summarize`(80자 발췌·sha8·PII flag) → `write_last_preview`(원문 0) | 화면 표시용 후보 | 없음(원문 미기록) |
| ⑤ commit | `cmd_hosted pull` → `_resolve_human_ctx` → `commit_bundle` → `prepare_selected` / `apply_pack_in_txn` | 단일 `BEGIN IMMEDIATE` 1회 write | **ledger.sqlite** |

- ⑤ 의 `_resolve_human_ctx` · `commit_bundle` 은 **Interactive Save 와 공유**한다(수렴 지점).
- ③ 의 DO `/drain` 은 read+delete 를 원자적으로 처리하는 **비보존(non-retention)** 동작이다.

---

## 4. Request(intent) / Event

### Request — intent (미저장 · DO 또는 로컬 staging 체류)

```
{
  schema_ver : 1,
  intent_id  : <16 hex>,
  text       : <문장 원문>,
  indices    : [ ... ],          // 1..64 범위 · 실효 ≤10
  confirm    : "SAVE i,j",
  created_ts : <epoch>,
  ttl_s      : 86400,            // 기본 86400 · 상한 604800
  source     : "hosted",
  speaker?   : "owner" | "ai"    // 선택
}
```

- 검증: `shapeReject` / `argsReject` / `_prevalidate`. TTL 만료 시 `.expired` 마킹 후 BLOCK.

### Event — commit 후 (node candidate=1)

```
node       { sentence, candidate=1, promotion_allowed=0, confirmed=0, state='active', speaker }
provenance { source_intent_id, bundle_id, actor_source }
audit      "hosted_bundle_commit"  ALLOW
```

- **원문 전문은 장부에 미저장** — 발췌 + 해시만 노드에 남는다(평문 잔존은 §6 참조).

---

## 5. ★ Canonicalization / 전달

### intent_id (16 hex)

```
intent_id = sha256( (text + "|" + indices.join(",") + "|" + confirm).encode() ).hexdigest()[:16]
```

- **TypeScript `intentHash` ≡ Python `intent_hash` 바이트 동일** 의무. 재해시 불일치 시 `intent_id_mismatch` 로 거부.
- **★ `speaker` 는 해시 재료에 미포함**(payload 로만 전달). 같은 text/indices/confirm 이면 speaker 가 달라도 intent_id 동일.

### bundle_id (별개 산식)

```
bundle_id = "bundle:" + sha256( sorted(intent_ids).join("|") ).hexdigest()[:16]
```

### 서명 이원화

| 구간 | 방식 | 재료 |
|---|---|---|
| **적재** `/mcp2` | 경로키 + Origin allowlist(claude.ai / chatgpt / openai) · **HMAC 없음** | — |
| **인출** `/save2/pull`(및 admin) | **HMAC-SHA256** (`X-BGP-TS` + `X-BGP-SIG`) · `SAVE_SIG_V2_ONLY=1` · `verifySig` 단일 출처 | `ts.METHOD.pathname.sha256(body)` · 시계 오차 ±300s |

- 적재는 경로키/Origin 로만 방어(HMAC 없음), 인출은 HMAC-SHA256 서명 왕복으로 방어. 서명 재료가 다른 이유는 두 구간의 위협 모델이 다르기 때문.

---

## 6. ★ 평문 잔존 (정직 — 계약 15)

본 프로필은 본문을 **암호화하지 않는다**. 평문이 어디에 남는지 정직하게 기록한다.

| 위치 | 평문 상태 | 소멸 조건 |
|---|---|---|
| ① hosted DO storage | 체류 | drain 시 delete(비보존) |
| ② 로컬 staging (`home/hosted_inbox/<id>.json`) | 원문 그대로 | pull 소비 후 |
| ③ **`_archive/<intent_id>.processed.json`** | **commit 후 원문 전체 영구 보존** | **별도 owner purge 만 삭제**(자동 삭제 아님) |

- **③ 이 핵심**: commit 이 끝나면 원문 전체가 `_archive/<intent_id>.processed.json` 에 **영구 보존**된다(계약 15). 이것은 "삭제"가 아니라 감사·재현용 아카이브이며 owner 가 명시적으로 purge 해야만 사라진다.
- 따라서 "장부에는 전문 미저장(발췌+해시만)"과 "로컬 평문이 `_archive` 에 영구 잔존"이 **동시에** 참이다. 둘 다 명시한다.
- **본문 암호화 없음** — 보호는 전송 구간 TLS + 인출 HMAC **무결성**뿐. 기밀성(암호화)은 제공하지 않는다. 보완책은 PII 백스톱 제외 · 짧은 TTL · pull 후 원격 잔존 0.

---

## 7. 상태 (persisted / derived)

- **persisted**: DO intent · 로컬 staging json · `_archive` processed · nodes(commit 후).
- **derived**: `summarize` 표시 상태(expired / pii flag) — 런타임 계산, 저장 안 함.
- ★ Hosted 고유 상태 전이는 없다. 최종 저장 게이트는 Interactive 의 `commit_bundle` 을 재사용(별도 산식 아님). 구 approval mint/consume(Trusted)은 저장 경로에서 제거됐고, approval core 는 비저장 mutation 별도 자산으로 무손상.

---

## 8. 공개 CLI vs 내부 전용

| 구분 | 항목 |
|---|---|
| **공개 CLI** | `hosted inbox` · `hosted pull --select --confirm` · `inbox --hosted` · MCP 도구(`save_intent` / `capture_preview` : text/indices/confirm/speaker). 노출 값 = `intent_id` · `ttl_s` · `saved`(text echo 0) |
| **내부 전용** | DO 엔드포인트 `/put` `/drain` · `intentHash` / `verifySig` / `commit_bundle` · HMAC secret(`SAVE_SIGN_SECRET`) · 경로키 · staging · `preview_ref` · bundle receipt(nonce) |

### UNSUPPORTED (현재 검증 표면 없음 · optional 아님)

- 실 worker HMAC 왕복(실배포 필요)
- DO `/drain` non-retention 실동작(배포 + 키 필요)
- 실 장부 저장(`--real-ledger` owner GO 필요)
- Cloudflare 1010 봇 차단 경로
- OAuth 핸드셰이크(README 상 OAuth 미구현)
- **본문 암호화**(없음 — 평문 체류 · TLS+HMAC 무결성만)

> UNSUPPORTED = "현재 미제공 검증 표면"이지 "optional(선택 제공)"이 아니다. 문서·vector 양쪽에서 UNSUPPORTED 로 명시하고 PASS 로 위장하지 않는다.

---

## 9. 배포 상태

- **`save_mcp`(`binggupack-save-intent-mcp`) 라인 = 현행 라이브** — **ChatGPT 채널** 대상. speaker 축 라이브.
- **v2 superseded** — 2026-07-03 대체됨(Cloudflare 삭제는 owner 대기). v1 은 이미 Cloudflare 삭제됨.
- **Claude 커넥터 = 삭제됨** — 재등록 시 최신 정책상 OAuth 요구 주의. **ChatGPT 채널은 유지.**
- dev 변형 deploy 금지 · `.prod.toml` 만 사용.
- (미확인: worker 실시간 HTTP 200 미확인[read-only 조사] · `.dev.vars.save_mcp` 키 미열람[repo 밖 gitignore] · selftest 미실행[CHANGELOG 인용].)

---

## 10. profile ID 표기

- **doc-profile 라벨: `hosted-relay`** — 문서용 네임스페이스. **해시 재료 아님.**
- **wire version: `schema_ver=1`**(intent payload). 문서는 이 매핑만 기술하고, 재현자는 실제 코드 재료 문자열을 그대로 사용한다.
