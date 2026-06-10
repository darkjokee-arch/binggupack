<!-- internal status tracker (운영 정책 상태 추적용, 사용자 안내 아님):
     marketplace=BLOCK / enum=HOLD / team_paid=DEFER / track1=GO(after fail-closed dry-run) -->

# BingguPack

> **Evidence-backed context packs for multi-agent AI workflows.**
> 여러 AI가 같은 작업 맥락을 이어받을 수 있게, 근거(evidence)가 붙은 context pack을 만들고 검증하는 도구입니다.
> **BingguPack is a public tool that each user can clone and run locally with their own data.**
> 각 사용자가 GitHub에서 받아 **자기 로컬 데이터**로 pack을 검증·저장·preview하는 공개형 개인용 도구입니다 (특정인 전용 아님).
> Track1 public RC · internal codename: OpenBinggu
> 버전: **v0.1.0-rc1** = read/dry-run + pack validation + MCP 5도구 → **v0.2.0-rc1** = +**local persistence**(candidate-only, opt-in, write 기본 OFF) → **v0.3.0-rc1** = +**manual one-shot capture**(read-only) → **v0.3.1-rc1** = +**batch pack staging loader** → **v0.4.0-rc1**(최신) = +**promotion preview**(read-only).
> 🚀 처음이라면: [10분 튜토리얼](docs/BINGGUPACK_TUTORIAL.md)
> "100% 완성판" 아님(아래 §범위). 코드 라이선스 = **MIT**. enum(release_mode/entitlement) HOLD · production write 0.

---

> ⚠️ **No author private data included / 작성자 개인 데이터 미포함**
> This repository contains framework/skeleton, validators, schema, synthetic fixtures, and toy examples only.
> 이 저장소는 프레임워크/스켈레톤·검증기·스키마·합성 fixture·toy 예시만 포함합니다. 작성자의 실제 그래프/DB/리뷰/캡처/evidence 원문은 포함하지 않습니다.

---

## What is BingguPack (Personal Track)

BingguPack(개인용 트랙)은 개인이 자기 로컬에서 작업 맥락을 **candidate pack**(검토 전 후보 묶음)으로 만들고, 그 pack을 **검증(dry-run)** 한 뒤, 원하면 **공개 가능한 형태로만** 다른 사람과 나눌 수 있게 하는 도구입니다.

- **개인용/로컬 우선**: pack은 기본적으로 `owner + AI` 내부에서만 동작합니다.
- **candidate-first / review-only**: 수집·생성물은 전부 candidate이며 자동 승격(promotion)되지 않습니다.
- **fail-closed 공개**: GitHub 공개 시 dirty/unknown source pointer는 **기본 차단(BLOCK)** 됩니다.

> 자동으로 운영 그래프/DB에 쓰거나, 외부로 실 데이터를 보내지 않습니다(HOLD).

**OpenCrab과의 역할 분리**: pack **생성**은 OpenCrab 도구 흐름(`opencrab.sh`)에서 수행합니다. BingguPack은 그렇게 만들어진 pack을 **검증(validate) → local staging 적재 → promotion preview**하는 공개 도구입니다. apply/finalize/upload는 별도 승인 단계이며 이 도구에 포함되지 않습니다.

---

## 이 공개본(RC)의 범위 / Scope of this public RC

이 저장소는 **BingguPack Track1 공개 1차판(public RC)** 입니다. **"BingguPack 100% 완성판"이 아닙니다.** 모든 사용자 환경에서의 동작을 보장하지 않습니다(자기 로컬 검증 필수, 아래 참조).

**이 RC가 제공하는 것 (read / dry-run / 검증 중심)**:
- pack **검증**(validate)·소비 smoke(consumer)·공개 fail-closed 게이트(publish_guard) — 전부 read/dry-run
- **MCP 기본판**: read/dry-run **5도구**(pack_build·pack_validate·consumer_smoke·publish_guard_dryrun·selftest) 노출, `inputSchema`·`tools/call content` MCP 표준 준수, write/upload/apply/push/confirmed 도구 **미노출**
- doctor **12/12** selftest, 공개 후보 트리 secret/PII scan(`--tree`)
- **(v0.2.0-rc1) local persistence**: 자기 로컬(`OPENBINGGU_HOME`)에 candidate graph 저장. **write 기본 OFF**·명시 opt-in 시에만·**CLI 전용(MCP write 도구 미노출)**·candidate-only(`promotion_allowed=0`). backup/rollback·C-2 1클릭·duplicate/freshness 검사·multi-user 격리. selftest 11/11 + read-only 재독 E2E 10/10.
- **(v0.3.0-rc1) manual one-shot capture (read-only)**: 사용자가 명시 지정한 경로만 capture(allowlist only·denylist 우선·fail-closed). raw 저장 0·source pointer 공개 미포함·rate limit·kill switch. **write opt-in 없으면 staging write 0**, **hook/daemon NOT_STARTED**(설치/실행 0). selftest 10/10. (reviewer/confirmed preview selftest는 의존성 검토 중·이번 RC 미포함.)

**이 RC가 아직 제공하지 않는 것 (다음 단계)**:
- **multi-agent handoff** 사용자 가이드·prompt template
- 실 **reviewer 인증·confirmed apply**(현재 preview까지, `confirmed_created=0`)
- **OpenCrab Pack v1 finalize**(Neo4j import/check/export 이벤트 플로우)
- **자동수집** daemon/hook

> write·upload·apply·confirmed·push는 이 RC에 노출되지 않으며, 각각 별도 승인·구현이 필요합니다.

## BingguPack full roadmap / 전체 로드맵

목표 로드맵은 아래 전체 흐름입니다. 이 공개 RC는 그 중 **입력 ~ pack 검증 ~ MCP 기본**까지를 read/dry-run으로 닫은 1차판입니다.

```
입력 → 핵심문장 노드 → 동사형 edge + evidence_refs → pack 생성/검증
  → local persistence(로컬 후보 저장) → multi-agent handoff(여러 AI 이어받기)
  → review/confirmed(사람 승인 후 확정) → OpenCrab Pack v1 finalize(필요 시 업로드)
```

| 흐름 | 공개 RC 상태 |
|---|---|
| 입력 / 핵심문장 / edge·evidence / pack 생성·검증 | ✅ read/dry-run 제공 |
| MCP read/dry-run 5도구(기본 도구 노출/호출 검증 완료) | ✅ 제공 |
| local persistence(로컬 후보 저장) | ✅ **v0.2.0-rc1** — candidate-only·opt-in·write 기본 OFF(selftest 11/11 + 재독 E2E 10/10) |
| manual one-shot capture(read-only) | ✅ **v0.3.0-rc1** — allowlist·denylist·rate limit·kill switch·fail-closed(selftest 10/10) |
| batch pack staging loader | ✅ **v0.3.1-rc1** — pack 디렉터리→staging apply→read-back→rollback(selftest 10/10) |
| promotion preview(read-only) | ✅ **v0.4.0-rc1** — D1~D4 변환·충돌·FTS/backup/rollback plan만, write 0(selftest 12/12) |
| multi-agent handoff | ✅ Phase 3 guide provided — [가이드](docs/OPENBINGGU_PHASE3_MULTI_AGENT_HANDOFF_GUIDE.md) |
| review / confirmed | 🔜 **v0.5.0-rc1 예정** — reviewer/confirmed **preview**(dry-run·sandbox·synthetic) 9모듈, confirmed_created=0·applied=0·promoted=0·upload=0 불변을 doctor가 강제. confirmed 생성·적용은 계속 별도 단계 |
| Hosted app / @BingguPack chat app | 📋 **planned** — 채팅 앱에서 `@BingguPack`처럼 pack context 호출. ChatGPT Apps/HTTPS MCP first, Claude/Gemini adapters later |
| Conversation → candidate capture (round-trip) | 📋 **planned** — 대화에서 사용자 승인 기반 candidate capture(preview 먼저, 자동 저장 없음) — [App path 설계 §8](docs/BINGGUPACK_APP_PATH_DESIGN.md) |
| OpenCrab Pack v1 finalize | ⏳ Neo4j 이벤트 플로우 미완 |

> Neo4j는 위 표의 마지막 단계(finalize/upload)에서만 필요합니다. 평소 일상 작업에는 불필요합니다(아래 §Neo4j 참조).
> Internal module structure may change between RC releases. / 내부 모듈 구조는 RC 릴리스 사이에 변경될 수 있습니다.

**사용 경로 3가지 / Three usage paths**:
1. **CLI/local path (현재 RC)** — 이 저장소의 도구로 자기 로컬에서 pack 검증·적재·preview.
2. **Mobile handoff path** — 모바일/웹 채팅에서는 pack 요약·prompt template을 붙여넣는 handoff fallback ([Phase 3 가이드](docs/OPENBINGGU_PHASE3_MULTI_AGENT_HANDOFF_GUIDE.md)).
3. **App path (planned)** — hosted MCP/App으로 채팅 앱에서 `@BingguPack` 호출. 플랫폼별 지원 차이 있음(ChatGPT Apps/HTTPS MCP 우선, Claude/Gemini는 adapter 필요).

---

## Requirements / 요구사항

- Python 3.10+ (표준 라이브러리 위주)
- OS: Windows / macOS / Linux (경로는 OS에 맞게)
- 외부 네트워크 불필요 (selftest는 모두 로컬 synthetic)

## Neo4j (when needed) / Neo4j는 언제 필요한가

- 개인영속/일상 작업은 **LocalCrab/BingguPack local store + JSONL/SQLite backend**로 동작합니다.
- **평소 Neo4j 서버는 불필요**합니다(서버 0, neo4j-cypher MCP 미등록).
- Neo4j는 **OpenCrab Pack v1 finalize/upload 시점에만 required**입니다 (`validate → Neo4j import/check → Neo4j graph export → package`).
- canonical graph = **JSONL**(`graph/nodes.jsonl`·`graph/edges.jsonl`). Neo4j는 그 시점의 검증/재현용이며 JSONL을 대체하지 않습니다.

## Install / 설치

macOS/Linux (bash):
```bash
git clone https://github.com/darkjokee-arch/binggupack.git
cd binggupack
python -m venv .venv
source .venv/bin/activate   # 선택
# 표준 라이브러리 위주이므로 별도 의존성 최소
```

Windows (PowerShell):
```powershell
git clone https://github.com/darkjokee-arch/binggupack.git
cd binggupack
python -m venv .venv
.\.venv\Scripts\Activate.ps1   # 선택
```

> ℹ️ 이 repo는 **Public**(최신 tag `v0.4.0-rc1`)이라 누구나 clone 할 수 있습니다.

## Run selftest / 자체 검증 실행

공개본이 정상 동작하는지 확인하는 가장 빠른 방법은 **doctor 한 명령**입니다. 모두 로컬 synthetic 데이터만 사용하며 운영 저장소를 변경하지 않습니다.

```bash
python scripts/openbinggu_doctor.py --selftest   # 공개 전 필수 검사 묶음(권장 진입점)
```

doctor는 아래 검사를 한 번에 호출하고 **요약(PASS/FAIL·reason_code·count)만** 출력합니다(raw 경로/secret 미출력):
1. scope_envelope_dryrun  2. watcher_pack_builder_m0  3. pack_validate
4. pack_consumer_smoke    5. path_safety_gate          6. mcp_path_gate_adapter
7. public_tree_scan       8. c2_guard_selftest(C-2 단일통제 21/21)
9. staging_write_selftest(temp DB, 운영 write 0)
10. phase4_reviewer_confirmed(preview 불변 강제: confirmed_created=0·applied=0·promoted=0·upload=0)
+ secret/PII scan(dry-run stub) + real_tree_scan(--tree 지정 시) + operating store 불변 확인
→ 총 **12/12 PASS · GATE=GO** 기대

개별 실행도 가능합니다:

```bash
python scripts/openbinggu_scope_envelope_dryrun.py --selftest
python scripts/watcher_pack_builder_m0.py --selftest
python scripts/openbinggu_pack_validate.py --selftest
python scripts/openbinggu_pack_consumer_smoke.py --selftest
python scripts/openbinggu_path_safety_gate.py --selftest
python scripts/openbinggu_mcp_path_gate_adapter.py --selftest
```

각 명령은 마지막에 `GATE: GO` 를 출력하고 종료코드 `0` 이면 통과입니다. 하나라도 `GATE: GO` 가 아니거나 종료코드가 0이 아니면 사용을 중단하고 이슈를 확인하세요.

## Use a pack / pack 사용 예시

다른 사람이 공개한 pack(또는 toy 예시)을 받아 읽는 흐름:

```bash
# 1) toy/synthetic pack 예시 위치
#    docs/fixtures_candidate/toy_public_pack_cross_root_read_ok.json
#    (다른 user_root 가 public pack 을 읽을 수 있는 synthetic 예시)

# 2) consumer smoke 로 읽기 흐름 확인
python scripts/openbinggu_pack_consumer_smoke.py --selftest
```

- 공개(public) pack은 owner가 아닌 다른 사용자도 읽을 수 있습니다(읽기 전용).
- private/team pack은 다른 user_root에서 읽으면 **거부(deny-by-default)** 됩니다.
- pack은 candidate이며, 받은 쪽에서 자동으로 자기 그래프에 병합되지 않습니다(검토 후 수동).

### Load a batch pack into local staging / batch pack을 로컬 staging에 적재해 보기

batch pack 디렉터리(manifest.json + jsonl)를 local persistence staging에 **apply → read-back → rollback(원복)** 까지 한 번에 검증:

```bash
python scripts/openbinggu_batch_pack_loader.py --selftest          # synthetic/temp 전 과정 검증 (10/10 기대)
# 자기 pack 적재(명시 opt-in 필수, 기본은 rollback 원복 / --keep 시 유지) — macOS/Linux:
OPENBINGGU_HOME=<repo 밖 경로> python scripts/openbinggu_batch_pack_loader.py --pack-dir <pack 디렉터리> --enable-write
```

Windows (PowerShell)는 환경변수를 먼저 설정합니다:
```powershell
$env:OPENBINGGU_HOME = "<repo 밖 경로>"
python scripts/openbinggu_batch_pack_loader.py --pack-dir <pack 디렉터리> --enable-write
```

- write는 **기본 OFF**(`--enable-write` 없으면 거부), apply 직전 PII/secret 잔존 재스캔(kind만 출력) 후 검출 시 거부됩니다.
- manifest가 `pack_type=candidate` + `promotion_allowed_default=false`가 아니면 load 자체를 거부합니다(fail-closed).

### Preview a promotion / 승격 전 preview (v0.4.0-rc1, read-only)

batch pack을 로컬 운영형 그래프로 승격하기 **전에**, 어떤 변환(D1~D4)·id 충돌·FTS 색인 추가·backup/rollback 준비가 필요한지 **미리 보여주는 plan 도구**입니다. target DB는 항상 read-only로만 열며 **어떤 write도 하지 않습니다**(승격 실행기는 이 RC에 미포함). `OPENBINGGU_OPERATING_DB` 미지정 시 synthetic temp DB로 시연합니다.

```bash
python scripts/openbinggu_promotion_preview.py --selftest                    # 12/12 PASS GATE=GO 기대
python scripts/openbinggu_promotion_preview.py --pack-dir <pack 디렉터리> --domain D10
```

> 변환 규칙·target schema contract·contentless FTS 검증법은 [Promotion Preview 설계](docs/OPENBINGGU_PROMOTION_PREVIEW_DESIGN.md) 참조.

## Multi-agent handoff / 여러 AI에 pack 넘기기

하나의 pack을 **Claude·Codex·ChatGPT·Gemini**가 같은 맥락으로 이어받게 하는 방법은 [Multi-agent handoff guide](docs/OPENBINGGU_PHASE3_MULTI_AGENT_HANDOFF_GUIDE.md)를 참고하세요. 4개 모델용 **prompt template**과 consumer 규칙을 포함합니다. 핵심:

- **evidence_refs 기반 답변**: pack의 evidence_refs로 뒷받침되는 사실만 답하고, 없으면 "pack에 근거 없음"이라고 답합니다(**추측 금지**).
- **evidence 없는 edge는 candidate**: 새 node→node 관계는 evidence 직접성이 없으면 confirmed가 아닌 candidate 제안으로만 표시합니다.
- **충돌 보존 / 자동 병합·승격 금지**: contradicts edge는 양쪽을 다 제시하고, 받은 pack을 자기 그래프에 자동 반영하지 않습니다(검토 후 수동, confirmed는 별도).
- 출처는 node_id/evidence_id만 표기(raw 경로·secret 미출력).

## Validate your real data / 실데이터 검증 (공개·업로드 전, 사용자 로컬에서만)

> ⚠️ selftest가 `GATE: GO`여도 그건 "검사기가 맞다"는 뜻이지 "당신의 실제 데이터가 안전하다"는 뜻이 아닙니다. 공개/업로드 전, **자기 로컬 데이터(공개 후보 트리)** 로 한 번 더 검증하세요. 이 검증은 **사용자 자기 머신에서만** 수행하며, 작성자/운영자가 당신의 데이터를 대신 스캔하지 않습니다.

1. clean repo 후보 트리 선택(공개 대상만 복사, 실 그래프/DB/.env 미복사)
2. `python scripts/openbinggu_doctor.py --selftest`
3. 공개 후보 트리 secret/PII scan: `python scripts/openbinggu_doctor.py --tree <공개_후보_트리>`
   (또는 `python scripts/openbinggu_public_tree_scan.py --tree <ROOT>`)
4. 결과는 **요약(count·reason_code·file_id)만** 확인 — raw 경로/원문/secret은 보지 않음
5. 검출 **1건 이상이면 verdict=BLOCK / GATE=NO-GO**(공개·업로드 차단)
6. 모두 0(CLEAN)일 때만, 요약을 보고 **본인이 수동 승인** → 그 후에만 push/upload

> 상세: [REAL_DATA_VALIDATION_PROCEDURE](docs/OPENBINGGU_REAL_DATA_VALIDATION_PROCEDURE.md). GitHub 공개와 OpenCrab 업로드는 동일 BLOCK 기준·동일 수동 승인 게이트. scan은 read-only이며 raw 경로/내용/secret을 출력하지 않습니다(file_id·reason_code·count만).

## Publish your own pack / 내 pack 공개 (요약)

공개 전 [Public Release Checklist](docs/OPENBINGGU_PUBLIC_RELEASE_CHECKLIST.md)를 전수 통과해야 합니다. 핵심:

1. 작성자 실데이터(그래프/DB/리뷰/캡처/evidence 원문) 미포함.
2. `.env`/token/key/credential 0, secret/PII scan PASS(검출 시 존재·길이만 보고, raw 미출력).
3. **source pointer가 dirty/unknown이면 공개 BLOCK**(비공개 절대경로·사내 URL·localhost·내부 IP 등).
4. 공개 직전 **owner 1회 수동 승인** 후에만 push.

> 자동 sanitizer/치환으로 통과시키지 않습니다(정책: 차단만 유지 — [SANITIZER_POLICY_BLOCK_ONLY](docs/OPENBINGGU_SANITIZER_POLICY_BLOCK_ONLY.md)).

## Do NOT / 금지·주의사항

- ❌ 실제 개인 데이터/실 경로(`C:\Users\<id>\...`)/사내 URL을 pack이나 repo에 넣지 마세요.
- ❌ `.env`·자격증명·토큰·DB/sqlite 파일을 커밋하지 마세요(`.gitignore` 확인).
- ❌ dirty/unknown source pointer를 강제로 공개하지 마세요(fail-closed 우회 금지).
- ⚠️ pack은 candidate입니다. 받은 pack을 운영 그래프에 자동 반영하지 마세요(검토 후 수동).
- ⚠️ 이 도구는 운영 그래프/DB write·외부 전송을 하지 않습니다. 그런 동작이 필요하면 별도 결정/구현이 필요합니다(현재 HOLD).

## License / 라이선스

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file.
이 프로젝트는 **MIT License**를 따릅니다(루트 `LICENSE` 파일 참조). Copyright (c) 2026 BingguPack contributors.

> 참고: `release_mode`·`entitlement` enum은 별개로 계속 HOLD입니다(코드 라이선스(MIT)와 무관, 배포/과금 모드 미확정).

## Status / 상태

- 개인용(트랙1): 로컬 사용 + 공개 준비(RC) — fail-closed dry-run 완료.
- 팀 유료(트랙2): DEFER. 불특정 다수 marketplace: BLOCK.
- 실제 GitHub: **Public 공개(최신 `v0.4.0-rc1`, prerelease).**
