marketplace=BLOCK / enum=HOLD / team_paid=DEFER / track1=GO(after fail-closed dry-run)

# BingguPack

> **Evidence-backed context packs for multi-agent AI workflows.**
> Track1 public RC (`v0.1.0-rc1`) · internal codename: OpenBinggu
> 상태: private repo 우선 공개 → GitHub UI 최종 확인 후 public 전환(별도 owner GO). "100% 완성판" 아님(아래 §범위).
> 코드 라이선스 = **MIT**(확정). enum(release_mode/entitlement) 확정 0 · production write 0.

---

> ⚠️ **No author private data included / 작성자 개인 데이터 미포함**
> This repository contains framework/skeleton, validators, schema, synthetic fixtures, and toy examples only.
> 이 저장소는 프레임워크/스켈레톤·검증기·스키마·합성 fixture·toy 예시만 포함합니다. 작성자의 실제 그래프/DB/리뷰/캡처/evidence 원문은 포함하지 않습니다.

---

## What is OpenBinggu (Personal Track)

OpenBinggu(개인용 트랙)는 개인이 자기 로컬에서 작업 맥락을 **candidate pack**(검토 전 후보 묶음)으로 만들고, 그 pack을 **검증(dry-run)** 한 뒤, 원하면 **공개 가능한 형태로만** 다른 사람과 나눌 수 있게 하는 도구입니다.

- **개인용/로컬 우선**: pack은 기본적으로 `owner + AI` 내부에서만 동작합니다.
- **candidate-first / review-only**: 수집·생성물은 전부 candidate이며 자동 승격(promotion)되지 않습니다.
- **fail-closed 공개**: GitHub 공개 시 dirty/unknown source pointer는 **기본 차단(BLOCK)** 됩니다.

> 자동으로 운영 그래프/DB에 쓰거나, 외부로 실 데이터를 보내지 않습니다(HOLD).

---

## 이 공개본(RC)의 범위 / Scope of this public RC

이 저장소는 **OpenBinggu Track1 공개 1차판(public RC)** 입니다. **"OpenBinggu 100% 완성판"이 아닙니다.** 모든 사용자 환경에서의 동작을 보장하지 않습니다(자기 로컬 검증 필수, 아래 참조).

**이 RC가 제공하는 것 (read / dry-run / 검증 중심)**:
- pack **검증**(validate)·소비 smoke(consumer)·공개 fail-closed 게이트(publish_guard) — 전부 read/dry-run
- **MCP 기본판**: read/dry-run **5도구**(pack_build·pack_validate·consumer_smoke·publish_guard_dryrun·selftest) 노출, `inputSchema`·`tools/call content` MCP 표준 준수, write/upload/apply/push/confirmed 도구 **미노출**
- doctor **11/11** selftest, 공개 후보 트리 secret/PII scan(`--tree`)

**이 RC가 아직 제공하지 않는 것 (다음 단계)**:
- 일반 사용자용 **local persistence** 플로우(저장 위치 정책·backup/rollback 자동화) — 현재는 staging 1-pack apply 실증만
- **multi-agent handoff** 사용자 가이드·prompt template
- 실 **reviewer 인증·confirmed apply**(현재 preview까지, `confirmed_created=0`)
- **OpenCrab Pack v1 finalize**(Neo4j import/check/export 이벤트 플로우)
- **자동수집** daemon/hook

> write·upload·apply·confirmed·push는 이 RC에 노출되지 않으며, 각각 별도 승인·구현이 필요합니다.

## OpenBinggu 100% 전체 로드맵 / Full roadmap

목표로 하는 "100% OpenBinggu"는 아래 전체 흐름입니다. 이 공개 RC는 그 중 **입력 ~ pack 검증 ~ MCP 기본**까지를 read/dry-run으로 닫은 1차판입니다.

```
입력 → 핵심문장 노드 → 동사형 edge + evidence_refs → pack 생성/검증
  → local persistence(로컬 후보 저장) → multi-agent handoff(여러 AI 이어받기)
  → review/confirmed(사람 승인 후 확정) → OpenCrab Pack v1 finalize(필요 시 업로드)
```

| 흐름 | 공개 RC 상태 |
|---|---|
| 입력 / 핵심문장 / edge·evidence / pack 생성·검증 | ✅ read/dry-run 제공 |
| MCP read/dry-run 5도구(기본 도구 노출/호출 검증 완료) | ✅ 제공 |
| local persistence(로컬 후보 저장) | ⏳ 다음 단계(staging 1-pack 실증만 완료) |
| multi-agent handoff | ⏳ 가이드 예정 |
| review / confirmed | ⏳ preview까지(confirmed는 사람 승인 기반) |
| OpenCrab Pack v1 finalize | ⏳ Neo4j 이벤트 플로우 미완 |

> Neo4j는 위 표의 마지막 단계(finalize/upload)에서만 필요합니다. 평소 일상 작업에는 불필요합니다(아래 §Neo4j 참조).

---

## Requirements / 요구사항

- Python 3.10+ (표준 라이브러리 위주)
- OS: Windows / macOS / Linux (경로는 OS에 맞게)
- 외부 네트워크 불필요 (selftest는 모두 로컬 synthetic)

## Neo4j (when needed) / Neo4j는 언제 필요한가

- 개인영속/일상 작업은 **LocalCrab/OpenBinggu local store + JSONL/SQLite backend**로 동작합니다.
- **평소 Neo4j 서버는 불필요**합니다(서버 0, neo4j-cypher MCP 미등록).
- Neo4j는 **OpenCrab Pack v1 finalize/upload 시점에만 required**입니다 (`validate → Neo4j import/check → Neo4j graph export → package`).
- canonical graph = **JSONL**(`graph/nodes.jsonl`·`graph/edges.jsonl`). Neo4j는 그 시점의 검증/재현용이며 JSONL을 대체하지 않습니다.

## Install / 설치

```bash
git clone <REPO_URL>            # repo 공개 후 (현재 HOLD)
cd openbinggu
python -m venv .venv && . .venv/bin/activate   # 선택
# 표준 라이브러리 위주이므로 별도 의존성 최소
```

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
+ secret/PII scan(dry-run stub) + real_tree_scan(--tree 지정 시) + operating store 불변 확인
→ 총 **11/11 PASS · GATE=GO** 기대

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

## Validate your real data / 실데이터 검증 (공개·업로드 전, 사용자 로컬에서만)

> ⚠️ selftest가 `GATE: GO`여도 그건 "검사기가 맞다"는 뜻이지 "당신의 실제 데이터가 안전하다"는 뜻이 아닙니다. 공개/업로드 전, **자기 로컬 데이터(공개 후보 트리)** 로 한 번 더 검증하세요. 이 검증은 **사용자 자기 머신에서만** 수행하며, 작성자/운영자가 당신의 데이터를 대신 스캔하지 않습니다.

1. clean repo 후보 트리 선택(공개 대상만 복사, 실 그래프/DB/.env 미복사)
2. `python scripts/openbinggu_doctor.py --selftest`
3. 공개 후보 트리 secret/PII scan: `python scripts/openbinggu_doctor.py --tree <공개_후보_트리>`
   (또는 `python scripts/openbinggu_public_tree_scan.py --tree <ROOT>`)
4. 결과는 **요약(count·reason_code·file_id)만** 확인 — raw 경로/원문/secret은 보지 않음
5. 검출 **1건 이상이면 verdict=BLOCK / GATE=NO-GO**(공개·업로드 차단)
6. 모두 0(CLEAN)일 때만, 요약을 보고 **본인이 수동 승인** → 그 후에만 push/upload

> 상세: [REAL_DATA_VALIDATION_PROCEDURE](OPENBINGGU_REAL_DATA_VALIDATION_PROCEDURE.md). GitHub 공개와 OpenCrab 업로드는 동일 BLOCK 기준·동일 수동 승인 게이트. scan은 read-only이며 raw 경로/내용/secret을 출력하지 않습니다(file_id·reason_code·count만).

## Publish your own pack / 내 pack 공개 (요약)

공개 전 [Public Release Checklist](OPENBINGGU_PUBLIC_RELEASE_CHECKLIST.md)를 전수 통과해야 합니다. 핵심:

1. 작성자 실데이터(그래프/DB/리뷰/캡처/evidence 원문) 미포함.
2. `.env`/token/key/credential 0, secret/PII scan PASS(검출 시 존재·길이만 보고, raw 미출력).
3. **source pointer가 dirty/unknown이면 공개 BLOCK**(비공개 절대경로·사내 URL·localhost·내부 IP 등).
4. 공개 직전 **owner 1회 수동 승인** 후에만 push.

> 자동 sanitizer/치환으로 통과시키지 않습니다(정책: 차단만 유지 — [SANITIZER_POLICY_BLOCK_ONLY](OPENBINGGU_SANITIZER_POLICY_BLOCK_ONLY.md)).

## Do NOT / 금지·주의사항

- ❌ 실제 개인 데이터/실 경로(`C:\Users\<id>\...`)/사내 URL을 pack이나 repo에 넣지 마세요.
- ❌ `.env`·자격증명·토큰·DB/sqlite 파일을 커밋하지 마세요(`.gitignore` 확인).
- ❌ dirty/unknown source pointer를 강제로 공개하지 마세요(fail-closed 우회 금지).
- ⚠️ pack은 candidate입니다. 받은 pack을 운영 그래프에 자동 반영하지 마세요(검토 후 수동).
- ⚠️ 이 도구는 운영 그래프/DB write·외부 전송을 하지 않습니다. 그런 동작이 필요하면 별도 결정/구현이 필요합니다(현재 HOLD).

## License / 라이선스

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file.
이 프로젝트는 **MIT License**를 따릅니다(루트 `LICENSE` 파일 참조). Copyright (c) 2026 OpenBinggu contributors.

> 참고: `release_mode`·`entitlement` enum은 별개로 계속 HOLD입니다(코드 라이선스(MIT)와 무관, 배포/과금 모드 미확정).

## Status / 상태

- 개인용(트랙1): 로컬 사용 + 공개 준비(RC) — fail-closed dry-run 완료.
- 팀 유료(트랙2): DEFER. 불특정 다수 marketplace: BLOCK.
- 실제 GitHub push: owner 승인 전 HOLD.
