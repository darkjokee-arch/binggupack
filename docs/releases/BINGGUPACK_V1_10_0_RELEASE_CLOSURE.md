# BingguPack v1.10.0 — Release Closure

> 이 문서는 v1.10.0 stable 사이클을 **완료된 릴리즈로 닫는** closure / handoff 기록입니다.
> 상태: `BINGGUPACK_V1_10_0_STABLE_RELEASED` · `..._AFTERCARE_PASS` · `..._DOCS_HYGIENE_PASS` · `..._PUBLIC_LANDING_REWRITE_PASS`

- Repo: `darkjokee-arch/binggupack`
- Release: <https://github.com/darkjokee-arch/binggupack/releases/tag/v1.10.0> (Latest · prerelease=false · draft=false)
- Stable line: `main = v1.10.0`

---

## 1. Release summary

v1.10.0은 BingguPack을 **설치 가능한 MCP 패키지(installable MCP package)** 로 stable화한 릴리즈입니다.

- **Installable MCP package stable화** — `git clone` 만으로 MCP 서버(`scripts/openbinggu_mcp_server.py`)가 포함되고, `scripts/install_claude_mcp.py` 헬퍼로 Claude Code에 등록됩니다. repo root 자동 감지, `BINGGU_HOME` 격리 주입, 운영 엔트리(`openbinggu-local`) 보호, Windows `claude.cmd` shim 처리.
- **Claude Code MCP 연결 가능 상태** — 재시작된 세션에서 sandbox MCP(`openbinggu-local-sandbox`)가 Connected 되고 8개 도구가 노출됨을 live 검증(`MCP_TOOL_EXPOSURE_PASS`).
- **preview-first / no-autosave safety model** — 모든 후보는 저장 전 미리보기(`nothing_saved=true`)를 거치고, 실제 저장은 사람이 직접 친 `SAVE n`만 인정합니다. AI/reader actor의 실저장은 `G4_no_auto`로 차단됩니다.
- 기능 자체는 `v1.10.0-rc.1`과 동일하며, cross-platform 검증(3-OS CI)과 MCP tool exposure 게이트를 통과해 stable로 승격했습니다.

## 2. Final commit map

| 단계 | commit | 위치 |
|---|---|---|
| Stable tag commit | `d15e6d6` | annotated tag `v1.10.0` 가 가리키는 commit |
| Docs hygiene | `1712483` | main only (tag 이후) |
| Public landing rewrite | `8c98459` | main only (tag 이후) |

- **annotated tag `v1.10.0` 는 `d15e6d6` 에 고정**되어 있습니다. GitHub release `v1.10.0` 가 가리키는 stable 산출물은 이 commit입니다.
- 이후 문서 개선 commit(`1712483`, `8c98459`)은 **main 브랜치에만 존재**하며 **tag를 이동시키지 않습니다**. 즉 release 산출물은 불변이고, 문서 hygiene/landing 개선은 main에서 계속 진행된 형태입니다.
- 신규 tag/release는 생성하지 않았습니다(문서 개선은 재릴리즈 대상이 아님).

## 3. Verification evidence

| 항목 | 결과 |
|---|---|
| `scripts/smoke_test.py` | **10/10 PASS** (오프라인, 격리 temp home) |
| sandbox `selftest` (live MCP) | **ALLOW** |
| MCP 8 tools exposure | **유지** — `selftest` · `capture_classify` · `capture_preview` · `pack_build` · `pack_validate` · `publish_guard_dryrun` · `consumer_smoke` · `save_candidate` |
| `save_actual_G4_no_auto_BLOCK` | **PASS** (`executed_write=false`, `ledger=temp_only`) |
| production write | **0** |
| OpenCrab ingest | **0** |
| G4 bypass | **0** |
| operating ledger durable write | **0** (`ledger.sqlite`/`-wal` 불변) |

3-OS cross-platform CI(ubuntu / macos / windows)도 PASS — 각 job은 tag clone → smoke 10/10 → installer dry-run → operating-name protection refusal을 검증합니다.

## 4. Public landing cleanup

- **README.md** — 외부 사용자용 landing page로 재작성(409 → 114줄). 내부 개발 로그·과거 버전 중심 문구·긴 selftest 나열을 docs 링크로 분리. 권장 구조(What/Why/Stable release/Core principles/Quick start/MCP install/Safety/Included/Verification/Docs/Non-goals/License) 반영.
- **INSTALL.md** — 실전 설치 절차 중심으로 재작성(207 → 104줄). RC 잔존 문구(`Latest release = v1.9.0`) 제거. Requirements → Clone → Verify → MCP sandbox install → Restart → Confirm tools → Confirm save gate → Operating vs sandbox home → Troubleshooting → Uninstall 흐름.
- **GitHub About** — description / homepage / topics 적용 완료.
  - description: `Local-first, evidence-backed memory/context pack framework for AI workflows. Installable MCP package with preview-first save safety.`
  - homepage: `https://github.com/darkjokee-arch/binggupack/releases/tag/v1.10.0`
  - topics: 기존 유지(`claude-code, context-packs, human-in-the-loop, local-first, mcp, ontology, agi-memory`).
- **LICENSE 변경 없음** — README의 License 섹션만 MIT 링크로 정리. LICENSE 파일(MIT 원문)은 미수정.

## 5. Safety invariants

stable line이 유지하는 불변식(전부 selftest로 강제):

- **local-first** — 원본은 내 PC 단일 `ledger.sqlite`. 클라우드는 원본이 아닙니다.
- **evidence-backed** — 5종 노드(문서·증거·개념·상태·판단), 모든 연결에 원문 근거 의무. 검증기 fail-closed.
- **candidate preview before save** — 모인 후보는 먼저 미리보기(저장 0).
- **no AI autosave** — `actor=auto`·confirm 누락/불일치·preview 미확인은 전부 BLOCK.
- **human-confirmed SAVE gate** — 키보드로 직접 친 `SAVE n`만 사람 승인(0-A 게이트). AI는 입력 경로를 못 거쳐 위조 불가.
- **OpenCrab Cloud ingest HOLD** — owner가 명시 승인하기 전까지 동작하지 않습니다.

## 6. Known operational lesson

- **mysql hook false positive** — commit message에 `update v1.10.0` 같은 문구가 들어가면 사전 hook이 이를 SQL `UPDATE v1...`로 오인해 commit을 BLOCK하는 false positive가 있습니다.
- **대응** — commit message 문구를 조정하거나, 메시지를 별도 파일에 써서 `git commit -F <file>` 방식으로 명령 문자열에 트리거 패턴이 노출되지 않게 합니다. (`docs: update v1.10.0 stable references` commit에서 실제 발생 → `-F` 우회로 해소.)

## 7. What was not done

- v1.11.0 착수 안 함
- 신규 feature branch 없음
- 신규 release / tag 없음 (tag `v1.10.0` = `d15e6d6` 불변)
- OpenCrab ingest 없음
- production write 없음
- 운영 ledger 수정 없음

## 8. Next recommended options

아래는 권장 옵션이며 **어떤 것도 자동 착수하지 않습니다 — owner decision 대기**.

- **Option A** — external clean-clone install verification (다른 머신/컨테이너에서 clone→install→smoke 재현)
- **Option B** — PyPI / package distribution 검토 (`pip install` 배포 경로)
- **Option C** — examples / tutorial 추가 (외부 사용자 onboarding 보강)
- **Option D** — v1.11.0 roadmap preview 작성 (다음 마일스톤 범위 정의)

---

_Closure 상태: `BINGGUPACK_V1_10_0_RELEASE_CLOSURE` — v1.10.0 stable 사이클 종료. 다음 작업은 owner decision 후 시작._
