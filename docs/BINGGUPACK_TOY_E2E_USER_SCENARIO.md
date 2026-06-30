> OpenBinggu is the legacy/internal codename for BingguPack.

marketplace=BLOCK / enum=HOLD / team_paid=DEFER / track1=GO(after fail-closed dry-run)

# BingguPack 1차 배포 — toy 효용 end-to-end 시나리오 (X2)

> **상태: toy E2E 시나리오 문서(2026-06-08). docs only · 모든 데이터 synthetic/toy · 실 업로드/push 0.**
> 4CLI 선결 gate X2(효용 증명, "껍데기" 탈피) 흡수. 상세 `BINGGUPACK_FIRST_RELEASE_4CLI_SYNTHESIS.md`(internal design doc — not included in public repo) §3-B.
> 상위: [FIRST_RELEASE_GITHUB_MCP_DESIGN](BINGGUPACK_FIRST_RELEASE_GITHUB_MCP_DESIGN.md) · `BINGGUPACK_PUBLIC_README_DRAFT.md`(internal design doc — not included in public repo).

---

## 0. 한 줄

GitHub에서 받은 사용자가 **OpenCrab 통합 없이도** 무엇을 할 수 있는지 toy 기준으로 끝까지 보인다: 받기 → 자가검사 → toy pack 만들기 → 검증 → 읽기 → (선택 시) 자기 OpenCrab 계정 업로드 후보. 우리 시스템의 store 자동 write/apply/ingest는 하지 않으며, 모든 예시 데이터는 synthetic/toy.

---

## 1. toy end-to-end 단계 (사용자 관점)

```
[1] 받기            git clone <REPO_URL>  (또는 zip download)
[2] 자가검사        python scripts/openbinggu_scope_envelope_dryrun.py --selftest
                    python scripts/watcher_pack_builder_m0.py --selftest
                    python scripts/openbinggu_pack_validate.py --selftest
                    python scripts/openbinggu_pack_consumer_smoke.py --selftest
                    → 각 GATE: GO / EXIT 0 확인 (모두 synthetic, 운영 store write 0)
[3] toy pack 빌드   examples/toy_project/ 를 입력으로 candidate pack 생성(temp)
                    → source pointer 판정(clean/dirty/unknown), 전부 candidate
[4] 검증            pack_validate → verdict 확인
[5] 읽기            pack_consumer_smoke → 다른 reader 관점으로 읽힘 확인
                    (tests/fixtures/synthetic/toy_public_pack_cross_root_read_ok.json 동형)
[6] 공개(선택)      fail-closed gate 통과 + owner/user 수동 승인 시에만:
                    (a) GitHub 공개 push  또는
                    (b) 사용자 자기 OpenCrab 계정 업로드 후보
                    → dirty/unknown source pointer·raw PII/secret/private path 있으면 BLOCK
```

- [1]~[5]는 **OpenCrab 없이도 성립**(로컬 자가검사+빌드+검증+읽기). 이것만으로도 "받아서 바로 쓸 수 있는" 효용을 보인다(D "껍데기" 우려 해소).
- [6]은 선택. 두 경로(GitHub push / OpenCrab 업로드) 모두 동일 fail-closed gate. **우리 시스템이 자동으로 store에 쓰지 않음** — 사용자가 수동 승인 후 직접.

---

## 2. toy로 보이는 "무엇을 할 수 있나" (효용)

| 효용 | toy 데모 |
|---|---|
| 작업 맥락을 candidate pack으로 구조화 | toy_project → pack(노드/엣지/evidence) 빌드 |
| 공개해도 되는지 자동 판정 | publish guard dry-run: dirty/unknown→BLOCK |
| 다른 사람이 받아 읽기 | cross-root public read(synthetic fixture) |
| 안전하게 자가검증 | 4 selftest GATE=GO, 운영 store 불변 |
| (선택) 자기 계정에 공유 | 사용자 주도 OpenCrab 업로드 후보(수동 승인) |

> 모든 데모 데이터 = synthetic/toy. 실 데이터·실 경로·secret 0. "현재 fixture/temp 기준 추가 노출 미검출."

---

## 3. 안 하는 것 (경계 명확화)

- OpenCrab store/graph **자동** write/apply/ingest 0 (우리 시스템이 자동으로 안 씀).
- GitHub push 자동화 0 / 실 업로드 0 (수동 승인 게이트 뒤).
- raw PII/secret/private path 출력 0.
- enum 확정·sanitizer 자동치환·team_paid·marketplace 0.

## 4. 상태

- X2 = **docs 시나리오 정의 완료**. 실제 MCP 서버/CLI 묶음(openbinggu doctor 등) 구현은 별도 GO.

## 5. 안전

docs only. synthetic/toy only. 실 업로드/push·production·OpenCrab 자동 write·apply/ingest/merge·enum·team_paid·marketplace·sanitizer·raw 출력 0. operating store mtime 불변.
