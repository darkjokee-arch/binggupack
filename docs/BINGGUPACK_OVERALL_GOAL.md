> OpenBinggu is the legacy/internal codename for BingguPack.

# BingguPack 상위 설계 목표 — Multi-User × Multi-Agent Shared Pack System

- 작성일: 2026-06-07
- 상태: **상위 목표 정의(설계 only)**. 코드 구현 0 / 실행 0 / OpenCrab write 0 / v09 freeze·ARMED OFF 유지.
- scope: project:openbinggu
- 위치: 모든 하위 설계(M0·MVP2.1 edge·운영모드·pack contract·common bus·release policy)의 **상위 기준**. 충돌 시 본 문서 우선.

---

## 0. 한 줄

BingguPack는 **각 사용자가 자기 작업/문서/코드/대화에서 evidence를 추출해 자기 node/edge graph를 만들고, GraphMerge review를 거쳐 자기 OpenCrab pack으로 만든 뒤, 그 pack을 여러 AI 실행환경(Claude Code·Codex·ChatGPT·Antigravity·CLI 모델 등)이 공유 context로 읽고 이어받게 하는** multi-user × multi-agent shared pack 시스템이다. 특정 사용자·특정 모델 전용이 아니다.

---

## 1. 최종 목표 (정정·확장)

> 이전 인식("특정 사용자/모델의 bid-engine 데이터를 pack으로")을 **폐기**하고 아래로 확장.

1. **사용자별 evidence graph 생성** — 사용자 A→A 전용, B→B 전용, C→C 전용. 각자의 작업/문서/코드/대화에서 evidence 추출.
2. **GraphMerge review** — 추출물은 전부 candidate. 사람 검토 전 어떤 것도 자동 승격 안 됨.
3. **OpenCrab pack 생성** — review 통과분을 pack(manifest + graph + evidence)으로.
4. **멀티에이전트 공유 context** — 같은 pack을 Claude Code·Codex·ChatGPT·Antigravity·CLI 모델 등 **여러 AI 실행환경이 공통으로 읽고 이어받음**.
5. **세션/모델 교차 맥락 유지** — 모델이 바뀌어도, 세션이 끊겨도 pack이 context를 운반 → 맥락 단절 0.
6. **pack 배포 범위** — private / team / public.
7. **공유 금지 경계** — secret / PII / raw 운영데이터는 어떤 범위로도 공유 금지.
8. **최종 UX — 앱에서 `@BingguPack` 호출** — 모바일 등 일반 채팅 앱에서 `@BingguPack`처럼 pack context를 불러 쓰는 것이 최종 사용 형태. hosted MCP/App 단계(planned)이며 플랫폼별 지원 차이 있음(ChatGPT Apps/HTTPS MCP 우선, Claude/Gemini는 플랫폼별 adapter 필요). 그전까지는 prompt/summary handoff가 모바일 fallback.
9. **역방향 round-trip (planned)** — 채팅 앱 대화에서 **사용자 명시 승인** 하에 핵심 문장만 BingguPack candidate로 capture (자동 저장 금지·raw 대화 전체 저장 금지·candidate-only). 미지원 플랫폼은 export/copy → CLI capture가 fallback.

---

## 2. 두 축

### 축 A — Multi-User (사용자별 격리)

- 사용자마다 **독립 evidence/node/edge graph + 독립 pack**. A의 데이터가 B pack에 섞이지 않음.
- 데이터 root는 사용자별 private. 엔진/프로토콜만 공용(openbinggu-core).
- pack 생성·review·promotion 결정은 각 사용자 권한 안에서만.

### 축 B — Multi-Agent (실행환경 공유)

- pack은 **모델 중립 포맷**(`opencrab-pack-v1`: manifest + graph + evidence). 특정 모델 SDK에 묶이지 않음.
- 소비 주체: Claude Code, Codex, ChatGPT, Antigravity, 임의 CLI 모델, MCP/REST/CLI tool layer.
- 한 모델이 만든 pack을 다른 모델이 읽어 **이어받기**(context handoff). = "세션/모델이 바뀌어도 맥락 유지"의 실체.

> 교차점: **사용자별 격리된 graph**가 **모델 중립 pack**으로 직렬화되어, **그 사용자의 여러 에이전트**가 공유한다. 격리(축 A)와 공유(축 B)는 충돌이 아니라 직교 — 공유는 항상 "한 사용자 소유 pack 안에서" 또는 "사용자가 명시 승인한 team/public 범위에서"만.

---

## 3. pack 배포 범위 (private / team / public)

| 범위 | 공유 대상 | 게이트 |
|---|---|---|
| **private** | 본인 + 본인 에이전트들 | 기본값. review 통과만 |
| **team** | 명시 초대된 구성원 | 사용자 명시 승인 + secret/PII redaction 재검증 |
| **public** | 누구나 | 사용자 명시 승인 + `BINGGUPACK_PUBLIC_RELEASE_POLICY`/`_CHECKLIST` 전수 통과 + raw 운영데이터 0 |

- 범위 승격(private→team→public)은 **별도 명시 승인 + redaction 재검증**. 자동 승격 없음.
- **공유 금지 영구 불변**: secret·PII·credential·.env·browser state·raw 운영데이터(bid-engine 로그 등)·production graph raw. 어떤 범위로도 금지.

---

## 4. 핵심 원칙 (모든 범위·모든 단계 공통)

1. **사용자별 데이터 격리** — graph/pack/review/promotion 권한 분리.
2. **secret/PII redaction** — 추출 시점 다단 마스킹, 잔존 1건 STOP.
3. **source pointer 정리** — 원문 전체 미복사, 경로 포인터 + 핵심 문장만. 포인터 생존 검증(dangling 차단).
4. **candidate-first / review-only** — 전건 candidate=true·promotion_allowed=false. 자동 promotion 금지.
5. **GraphMerge 검토 후 pack 생성** — review 큐 통과 없이 pack 확정·공유 없음.
6. **pack = manifest + graph(node/edge) + evidence 동반** — 셋 중 하나라도 없으면 불완전 pack.

---

## 5. 표준 파이프라인 (사용자 1인 기준, 모든 사용자 동형)

```
작업/문서/코드/대화
   → [capture]        원문 미복사 + 1차 redaction (사용자별 격리)
   → [evidence]       evidence_chunk (source pointer + 핵심문장, 2차 redaction)
   → [node/edge]      candidate node + (review 승격 후) candidate edge
   → [GraphMerge review]  match_policy(node)·classify_edge_pair(edge) → review_candidates 큐 → 사람 검토
   → [pack 생성]      reviewed 결과 → manifest + graph + evidence pack (pack contract validate)
   → [배포]           private / (승인 시) team / (전수 통과 시) public
   → [멀티에이전트 소비]  Claude Code·Codex·ChatGPT·Antigravity·CLI 가 pack을 공유 context 로 읽고 이어받음
```

- production graph 반영·OpenCrab write·v09 해제는 **별도 중대결정**. 본 파이프라인의 capture~pack까지는 temp/staging dry-run으로 검증 가능.

---

## 6. 기존 컴포넌트 정렬 (이 목표 관점 재배치)

| 컴포넌트 | 이 목표에서의 역할 | 상태 |
|---|---|---|
| **Watcher M0~M3 운영모드** (`BINGGUPACK_WATCHER_READONLY_OPERATING_MODE_DESIGN.md`(internal design doc — not included in public repo)) | 축 A 입력단 — 사용자 작업 → capture→evidence→node. 사용자별 격리된 evidence 생산자 | M0 구현 GO 완료 / M1~M3 HOLD |
| **MVP2.1 edge** (`BINGGUPACK_MVP21_EDGE_SAFETY_FILTER_DESIGN.md`(internal design doc — not included in public repo)) | graph의 edge 절반 — node/edge graph 완성. R2 안전필터 | 설계 R2 / 구현 HOLD |
| **pack contract + validator** ([PACK_CONTRACT](BINGGUPACK_PACK_CONTRACT.md)) | 모델 중립 pack(축 B) 의 최소 계약 게이트. private/team/public 공통 형식 보증 | 운영 중 |
| **M0→pack 빌더** (`watcher_pack_builder_m0.py`) | capture~pack 직렬화 dry-run — 축 A 산출을 축 B 포맷으로 | dry-run GO 완료 |
| **Common Bus** (`BINGGUPACK_COMMON_BUS.md`(internal design doc — not included in public repo)) | 축 B 실체 — 여러 모델이 같은 Core 통해 graph 기여/조회. 엔진 공개·데이터 private | 정의됨 |
| **Public Release Policy/Checklist** ([POLICY](BINGGUPACK_PUBLIC_RELEASE_POLICY.md)·[CHECKLIST](BINGGUPACK_PUBLIC_RELEASE_CHECKLIST.md)) | public 범위 게이트 — raw 데이터 공유 금지 강제 | 정의됨 |
| **v0.11~v0.17 chain** (staging→review queue→decision→apply plan→e2e) | GraphMerge review~apply 백본 — 파이프라인 5단계 review 부분 | 설계/부분구현 |
| **EVALUATION_PROTOCOL** (`BINGGUPACK_EVALUATION_PROTOCOL.md`(internal design doc — not included in public repo)) | pack/시스템 가치를 데이터 축적 후 고정 기준으로 측정 | 12질문 조건부 A_KEEP |

---

## 7. 현재 위치 / 남은 갭 (multi-agent shared pack 기준)

| 파이프라인 단계 | 상태 |
|---|---|
| capture → evidence | ✅ M0 |
| evidence → candidate node | ✅ M0/MVP2 |
| evidence → candidate edge | ⏳ MVP2.1 구현 HOLD |
| GraphMerge review (node) | 🟡 match_policy review-only 검증 / 운영 review 큐 실적재 미완(v0.12) |
| pack 생성(manifest+graph+evidence) + validate | ✅ skeleton dry-run (edge 0) |
| **사용자별 격리 다중화** (A/B/C 독립 root) | ⏳ 미설계 — 현재 단일 사용자 root 가정 |
| **모델 중립 pack 소비 인터페이스** (멀티에이전트 read/이어받기) | ⏳ 미설계 — pack 포맷은 있으나 소비측 표준(MCP/REST read contract) 미정의 |
| **private/team/public 범위 메타 + 승격 게이트** | ⏳ 부분(public policy 존재) / team·범위 메타 pack 필드 미정의 |
| **세션/모델 교차 맥락 유지 실증** | ⏳ 미실증 |

---

## 8. 불변 안전 원칙 (목표가 확장돼도 유지)

- secret/PII/raw 운영데이터 **공유 금지** — private 포함 어떤 범위도 raw 노출 0.
- candidate-first / review-only / 자동 promotion 0.
- 사용자별 격리 — A↔B 데이터 혼입 0.
- production write·OpenCrab ingest·v09 해제·ARMED = **별도 중대결정** (본 목표 확장으로 자동 개방 아님).
- pack 공유는 review 통과 + 범위별 redaction 재검증 후에만.

---

## 9. 다음 설계 후보 (전부 별도 GO)

1. **모델 중립 pack 소비 contract** — Claude Code·Codex·ChatGPT·Antigravity·CLI 가 pack을 read/이어받는 표준 인터페이스(MCP/REST read schema). 축 B 핵심 미싱링크.
2. **pack 범위 메타** — manifest에 `visibility: private|team|public` + 범위 승격 게이트 + 범위별 redaction 재검증 규칙.
3. **사용자별 격리 root 설계** — A/B/C 독립 data root + 권한 분리 + pack id 네임스페이스.
4. **세션/모델 교차 맥락 유지 PoC** — 한 모델이 만든 pack을 다른 모델이 읽어 이어받는 dry-run.
5. (선행 의존) MVP2.1 edge 구현 / GraphMerge review 큐 실적재(v0.12).

---

## 10. 실행 0 확인

본 문서 작성으로 발생: **상위 목표 docs 1개 write**. 그 외:
코드 구현 0 / 실행 0 / 기존 docs·스크립트 수정 0 / OpenCrab write·ingest 0 / production graph 0 / store write 0 / v09 해제·ARMED 변경 0 / bid-engine 변경 0.
