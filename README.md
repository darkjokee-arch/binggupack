# BingguPack

**대화에서 건진 판단을, 사람이 도장 찍어 쌓는 개인 지식장부.**
AI와의 대화·메모에서 건질 문장(판단/상태/개념)을 후보로 떠서, 사람이 직접 confirm 문구를
타이핑해야만 저장·기각·수정·수용되는 **로컬 우선(local-first) 후보 관리 시스템**입니다.

- 최신 공개판: **v1.1.0** (그래프 문법 스펙 + 전 팩 원문 검색. v1.0.0 = 개인용 정식 동결, v1.0.1 = save preview-id 강제)
- 자동으로 되는 것: **없음.** 모든 변경은 사람의 정확한 confirm 문구가 게이트입니다.
- 절대 안 하는 것: 원문 전문 저장(발췌만) · 자동 확정(confirmed 0) · 자동 업로드.

## 3분 시작 / Quick start

```bash
git clone https://github.com/darkjokee-arch/binggupack
cd binggupack
python scripts/openbinggu_doctor.py --selftest   # 12/12 GATE GO 확인

python binggu.py init                            # 내 장부 생성 (~/.binggupack/ledger.sqlite)
python binggu.py preview "이 입찰은 마진이 낮아 보류한다. 백필 작업이 진행 중이다."
# → 후보 표 + preview_id 가 표시됨. 번호를 직접 고른 뒤:
python binggu.py save "이 입찰은 마진이 낮아 보류한다. 백필 작업이 진행 중이다." \
                 --preview-id <표시된 id> --pick 1,2 --confirm "SAVE 1,2"
python binggu.py list                            # 도장·id·문장 발췌 목록
python binggu.py status
```

**저장 흐름(고정)**: "저장하고 싶다"는 의도는 preview의 시작일 뿐, 실행 승인이 아닙니다.
① 의도 → ② `preview` 생성 → ③ preview_id 발급 → ④ 후보 번호 표시 → ⑤ **사람이 번호 선택** →
⑥ confirm 문구 입력 → ⑦ 그때 저장. 의도만으로 자동 저장·raw text 직행 save·번호 없는 save·
preview_id 없는 save는 전부 BLOCK 됩니다. 저장되는 것은 80자 이내 발췌뿐입니다.
외부 사실(릴리스 상태·업로드 여부·등급 등)은 실측 확인과 본인 판단이 일치할 때만 저장하세요.

## 후보 관리 / Candidate management

목록의 `#`(번호)와 `id`(8자리)를 함께 적어야 어떤 변경도 통과합니다 — 목록이 바뀌면 자동 차단.

| 하고 싶은 것 | 명령 (confirm 형식) |
|---|---|
| 기각 (보존+기본조회 제외) | `binggu.py deprecate <n> <id8> --reason "..." --confirm "DEPRECATE <n> <id8>"` |
| 수정 (in-place 금지: 기각+신규 묶음) | `binggu.py replace <n> <id8> --with "<수정문장>" --reason "..." --confirm "REPLACE <n> <id8> WITH <수정문장>"` |
| 수용 기록 | `binggu.py accept <n> <id8> --reason "..." --confirm "ACCEPT <n> <id8>"` |
| 수용 철회 (보존형) | `binggu.py unaccept <n> <id8> --reason "..." --confirm "UNACCEPT <n> <id8>"` |
| 검증 예정일 등록 | `binggu.py due <n> <id8> --date 2026-07-01` |
| 검증 결과 기록 (4값) | `binggu.py resolve <n> <id8> --outcome 성공\|실패\|불확실\|판정불가 --reason "..."` |
| due 경과 리마인드 | `binggu.py reminders` |

설계 원칙:
- **기각은 삭제가 아닙니다** — 물리 보존 + 기본 조회 제외(언제든 추적 가능).
- **수정은 덮어쓰기가 아닙니다** — 원본 기각(`replaced_by` 역링크) + 수정본 신규 저장. 같은 내용
  재생성은 유니코드 정규화 해시로 차단됩니다.
- **수용(owner_accepted)은 확정이 아닙니다** — append 이벤트 기록일 뿐, 노드는 1바이트도 안 변하며
  철회·재수용 이력이 전부 남습니다. `confirmed`는 이 시스템에 존재하지 않습니다.
- **검증 결과는 기록일 뿐입니다** — `실패`를 줘도 자동 강등되지 않습니다(강등은 사람의 별도 기각).

## 안전 불변식 / Safety invariants

매 변경마다 강제되고, 전부 selftest로 증명됩니다(약속이 아니라 테스트):

- 원문 전문 저장 0 (문장 발췌만) · PII/secret/사업자번호 저장 게이트 재스캔
- candidate-only (`promotion_allowed=0` 전수) · 자동 강등/확정/업로드 0
- 모든 변경 전 스냅샷 + checksum rollback (중간 실패 = 원복)
- append-only audit chain (변조 시 BROKEN 검출)

## 검증 / Verify (실측 기대값)

```bash
python scripts/openbinggu_doctor.py --selftest                      # 12/12
python binggu.py --selftest                                         # 14/14 (CLI 풀 사이클)
python scripts/openbinggu_v1_candidate_cycle_real_once.py --dry-run-temp   # 17/17 (통합 사이클)
python scripts/openbinggu_conversation_candidate_save.py --selftest # 12/12 (저장 게이트)
python scripts/openbinggu_candidate_list_view.py --selftest         # 13/13 (목록)
python scripts/openbinggu_candidate_deprecate_ux.py --selftest      # 15/15 (기각)
python scripts/openbinggu_candidate_replace_ux.py --selftest        # 16/16 (수정)
python scripts/openbinggu_owner_accept_ux.py --selftest             # 16/16 (수용)
python scripts/openbinggu_v08_review_resolve_4values.py --selftest  # 16/16 (4값 resolve)
python scripts/openbinggu_save_intent_outbox_runner.py --selftest   # 16/16 (save-intent outbox)
python scripts/openbinggu_upload_preflight.py --selftest            # 37/37 (업로드 preflight)
python scripts/openbinggu_v08_real_cycle_once.py --dry-run-temp     # 14/14 (쓰기 루프)
python scripts/openbinggu_public_tree_scan.py --tree .              # CLEAN
```

요구사항: **Python 3.10+** 표준 라이브러리만(외부 의존성 0). Windows/macOS/Linux.
더 자세한 절차는 [INSTALL.md](INSTALL.md), 따라하기는 [docs/BINGGUPACK_TUTORIAL.md](docs/BINGGUPACK_TUTORIAL.md).

## Graph Grammar — pack이 따라야 하는 그래프 문법 (v1.1.0)

모든 pack은 [docs/BINGGUPACK_GRAPH_GRAMMAR_SPEC.md](docs/BINGGUPACK_GRAPH_GRAMMAR_SPEC.md)의 문법을 따르며,
검증기가 fail-closed로 강제합니다:

- **노드 5종**(문서/증거/개념/상태/판단), 전부 **핵심 문장형** — 단어 노드 금지
- **엣지는 동사형만**(predicate registry v1) — "이 증거는 이 노드의 근거가 된다" 등 6종
- **전 엣지 원문 증빙 의무** — accepted 엣지는 원문 근거(출처+행+발췌 해시) 1개 이상.
  파서/폴더/frontmatter 유래 정보는 증거가 아니라 출처표시(provenance)
- **검증 미달 = 거부가 아니라 보류(quarantine)** — 사유+복귀 조건과 함께 보존, 매 빌드 자동 재심사.
  즉시 차단은 PII/시크릿뿐
- payload에는 짧은 라벨만, 전체 문장은 evidence chunk에 — **절단은 빌드 실패**

## Pack — 장부를 묶어서 옮기기

장부/문서를 **pack**(jsonl 묶음)으로 만들어 검증·공유할 수 있습니다. pack은 언제나 candidate이며,
받는 쪽 운영 그래프에 자동 반영되지 않습니다.

```bash
# toy pack 읽기 흐름 확인
python scripts/openbinggu_pack_consumer_smoke.py tests/fixtures/synthetic/toy_public_pack_cross_root_read_ok.json
# 공개·업로드 전 fail-closed 게이트 (G1~G7)
python scripts/openbinggu_upload_preflight.py <pack_dir> [<temp_staging_db>]
```

업로드 preflight는 **승인 문구를 사람이 직접 타이핑**해야 하고(`UPLOAD <pack_id> <hash8> IRREVERSIBLE`),
전송 직전 full SHA-256 재검증에 실패하면 무조건 중단됩니다. 실 전송은 의도적으로 미구현(별도 결정)입니다.

## 현재 제공 / Planned

| 영역 | 상태 |
|---|---|
| 로컬 후보 관리(저장→기각→수정→수용→4값 검증) | ✅ v1.0.0 — real 1사이클 13/13 + clean clone 17/17 검증 |
| `binggu` CLI (내 장부 진입점) | ✅ v1.0.0 |
| 그래프 문법 스펙(5종 노드·동사 엣지·증빙 의무) + 검증기 강제 | ✅ v1.1.0 — [spec](docs/BINGGUPACK_GRAPH_GRAMMAR_SPEC.md), 엣지 증빙 런타임 강제 |
| hosted 조회 (Claude/ChatGPT 채팅에서 장부 read-only) | ✅ 동작 검증됨 — v1.1.0부터 **전 팩·원문 검색**(`evidence_search`에 pack_id 불필요). 각자 자기 워커를 배포(`hosted/`), 공용 서버 없음 |
| hosted 저장(save-intent) | 🔜 planned — 설계 완료(`docs/BINGGUPACK_HOSTED_SAVE_INTENT_DESIGN.md`), 로컬 outbox 러너까지 구현. live 노출은 인증·canary·audit·rollback 게이트 통과 후 별도 결정 |
| OpenCrab private 업로드 | 🔜 planned — preflight(G1~G7)까지 구현·검증, 실 전송은 별도 결정 |
| 팀/공유/마켓플레이스/과금 | ❌ 범위 밖 (정책 미정) |

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

- 개인용(트랙1): **v1.1.0 — 후보 관리 전 구간 완성(v1.0.0) + 그래프 문법 스펙·전 팩 원문 검색(v1.1.0)**. `binggu` CLI로 개인 장부 실사용 가능.
- 팀 유료(트랙2): DEFER. 불특정 다수 marketplace: BLOCK.
- 실제 GitHub: **Public 공개(최신 `v1.1.0` Latest).**
