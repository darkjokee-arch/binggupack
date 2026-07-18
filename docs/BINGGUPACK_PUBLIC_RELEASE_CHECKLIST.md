---
status: superseded
superseded_by: binggupack.pack.scope_envelope (classify_source_pointers) · MCP publish_guard_dryrun · binggupack.pack.doctor --tree
---

> ⚠ 2026-06 1차 GitHub 공개 **전** 시점 문서 — 1차 공개(2026-06, v1.22.0 시점 공개 repo darkjokee-arch/binggupack)는 이후 완료됨. 당시 게이트 기록으로 보존. 현행 공개 fail-closed 게이트 정본: `binggupack.pack.scope_envelope`(classify_source_pointers, MCP publish_guard_dryrun 결선) · `binggupack.pack.doctor --tree`.

> OpenBinggu is the legacy/internal codename for BingguPack.

# BingguPack Public Release Checklist

> **상태라인(표준):** `marketplace=BLOCK / enum=HOLD / team_paid=DEFER / track1=GO(after fail-closed dry-run)`
>
> GitHub 공개 **전** 필수 점검. 하나라도 실패 시 공개 금지.
> 본 체크리스트 자체는 공개 가능(데이터 없음).

## 0. fail-closed 공개 게이트 (2026-06-08 반영)

> 트랙1 GitHub 공개의 선결 안전판. 상세 `BINGGUPACK_TRACK1_FAILCLOSED_PUBLISH_GUARD_DESIGN.md`(internal design doc — not included in public repo).

- [ ] **source pointer dirty/unknown → publish BLOCK** — pack builder(`watcher_pack_builder_m0`)가 source pointer를 `clean | dirty | unknown` 판정. dirty(Windows 절대경로/`file://`·UNC·비공개 unix path·localhost·내부IP·내부도메인) 또는 unknown(빈값·토큰)이 1건이라도 있으면 공개 BLOCK(raw 경로 미출력·라벨·count만).
- [x] **`scripts/openbinggu_scope_envelope_dryrun.py --selftest` GATE=GO / EXIT=0** (게이트1 마스킹 clean-only·게이트2 수동승인·회귀방지 R1~R3·source pointer 판정).
- [x] **`scripts/watcher_pack_builder_m0.py --selftest` GATE=GO / EXIT=0** (source pointer 판정 → publish BLOCK 연결, operating_store 불변).
- [ ] **자동 sanitizer/치환은 HOLD (정책 확정 = 차단만 유지)** — 자동 치환으로 공개 허용하지 않음. 차단 내역은 raw 없이 `reason_code`/`count`/`source_pointer_id`로만 확인. 사용자가 직접 확인·승인한 항목만 **수동 whitelist 예외**(기본 허용 아님, 범위·만료·승인자 기록 필수, 실제 구현 HOLD). 상세 [SANITIZER_POLICY_BLOCK_ONLY](BINGGUPACK_SANITIZER_POLICY_BLOCK_ONLY.md).
- [ ] **실제 GitHub push는 owner 명시 승인 전 HOLD** — 게이트1 통과 + owner 1회 명시 approve(1 pack/1 push, 이전 승인 재사용 금지) 후에만 push 후보 진입.

## 공개 전 체크리스트

- [ ] private graph 파일 없음 (`localbinggu_production_graph.yaml` 등)
- [ ] sqlite/db 파일 없음 (`*.sqlite` / `*.db` / `localcrab_index.sqlite`)
- [ ] real reports 없음 (`reports/` 실데이터)
- [ ] real reviews 없음 (`reviews/` 실 decision)
- [ ] real captures 없음 (`captures/`)
- [ ] real evidence 없음 (실 `evidence_index`)
- [ ] `reingest_pack_draft/` 실원본 없음
- [ ] `.env` / token / key / credential 없음
- [ ] 실제 경로 / 사용자명 / 프로젝트명 등 민감 정보 없음
- [ ] synthetic fixture만 포함 (`tests/fixtures/synthetic/`)
- [ ] toy example만 포함 (`examples/toy_project/`)
- [ ] `.gitignore` 적용 확인 (§5 정책)
- [ ] secret scan PASS (repo 전체 rglob + secret 정규식 → 0건)
- [ ] README에 private data 미포함 문구 있음 (EN/KO)

## 자동 점검 권장 명령(공개 repo 기준 — 본 단계 미실행)

```
# secret/PII rglob 스캔 (count/매칭파일만, 원문 금지)
# *.sqlite / *.db / production_graph.yaml / reingest_pack_draft 존재 여부
# .gitignore 매칭 누락 파일 git check-ignore 검증
```

> 참조: [BINGGUPACK_PUBLIC_RELEASE_POLICY.md](BINGGUPACK_PUBLIC_RELEASE_POLICY.md)
