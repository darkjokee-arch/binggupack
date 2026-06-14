# BingguPack v1.5.1 — Release Note (DRAFT, 태그/릴리스 전)

> 상태: **draft**. 태그/릴리스 미실행. OpenCrab Cloud upload/ingest/owned 확인 0.
>
> **버전 정합(실측)**: 원격 tag `v1.5.0` = `d35751a` (P1~P8 + graph hardening, **발행 완료·미수정**).
> 이번 v1.5.1 후보 = HEAD 기준 그 위에 cross-platform 1트랙 추가. **기존 v1.5.0 태그는 수정/삭제/재태그하지 않습니다.**
> v1.5.1 태그는 아직 **미발행**입니다(owner GO 대기).

## 한 줄
v1.5.0(로컬 publish pipeline + evidence graph) 위에 **Windows/WSL/macOS cross-platform 지원**을 더한 하위호환 패치 후보. 저장·확정·업로드 게이트는 그대로 사람 confirm 전용.

## 버전
- 이전 발행: **v1.5.0** (tag `d35751a` — P1~P8 + graph hardening)
- 이번 후보: **v1.5.1** (cross-platform home support, 미발행)
- 성격: **patch** — 하위호환, 기존 도구/스키마/장부 포맷 변경 0, 기존 Windows 동작 100% 보존

## 추가 (Added) — Cross-platform 지원 (Windows / WSL / macOS)
- 정책 단일 원천 `scripts/binggu_platform.py` + selftest `scripts/binggu_platform_selftest.py`(36/36).
- 기본 = OS별 로컬 홈(Windows `%USERPROFILE%\.binggupack`, WSL/macOS `~/.binggupack`).
- OS 간 같은 장부 공유는 `BINGGU_HOME` 명시(opt-in) — 자동 추측·자동 마이그레이션 0.
- 동시 실행은 `<ledger>.lock`(O_EXCL) + `busy_timeout`으로 **fail-closed**.
- python 런처 안내: Windows `py`, WSL/macOS `python3`. hook 등록 명령도 OS별.
- 기존 Windows 동작 보존(BINGGU_HOME 미설정 시 종전 경로와 동일 — selftest로 확증).
- 문서: `docs/BINGGUPACK_CROSS_PLATFORM_SUPPORT.md`.

## 변경 (Changed)
- `binggu.py`·`binggu_publish_p3/p5/queue_p1`·`binggu_semantic_shadow` — 장부 경로를 `BINGGU_HOME` 우선 helper로 통일(Windows 동작 보존).
- `README.md`·`INSTALL.md` — cross-platform 사용법·검증·런처 분기 반영, 검증 기대값 실측 정합(`binggu --selftest` 26/26, publish P1~P8 검증 섹션 추가).

## 포함 (Inherited from v1.5.0 — 참고)
- **PC-mediated read publish pipeline P1~P8**: queue/멱등잠금/상태머신(P1) · 빌더·검증기·영구금지 22~27(P2) · 실 ledger read-only 게이트(P3) · data_class 분리(P4) · candidate→active promote(P5/P7) · OC12 ZIP repair(P6) · 회귀 묶음 러너(P8).
- **Evidence graph hardening**: 5종 canonical 노드 + semantic_subtype 보조층 + 동사형 typed edge(`supports_judgment` 화이트리스트) + 모든 엣지 원문 증빙 의무 + fail-closed 강제. 후보/확정 분리(`real_active`만 release 자격).

## 명시적 비포함 (HOLD)
- ❌ OpenCrab Cloud upload / ingest / owned pack 확인 / 재시도 — 전부 보류
- ❌ Cloud 원본화(양방향 sync) · marketplace · 팀/공유/과금
- ❌ 자동 확정(`confirmed`) · 자동 업로드 · 상주 데몬 · 주기적 자동 pull
- ❌ cos/확률 지표로 capture/save/approve 결정하는 자동화 (subtype은 표시·추천 보조층)
- ❌ 실 ledger 비가역 write(promote는 owner 명시·백업·audit 전제)
- ❌ DB insert · **tag/release**(owner GO 대기) · 기존 v1.5.0 태그 재태그

## 검증 (실측, 문서 정리 후)
- `binggu_publish_run_all_selftests` — **8/8 PASS · REGRESSION=GO** (P1 27/27·P2 26/26·P3 11/11·P4 15/15·P5 17/17·P6 19/19·cloud_pack GO·tree scan CLEAN)
- `binggu_platform_selftest` — **36/36 GATE=GO**
- `binggu.py --selftest` — **26/26 GATE=GO**
- `openbinggu_doctor.py --selftest` — **15/15 GATE=GO**
- `openbinggu_public_tree_scan --tree .` — **hits=0 · CLEAN**
- 개인정보/민감정보 정적 스캔: 0건. 실 ledger write 0 · Cloud/DB/ingest 0.

## OpenCrab 상태 (참고)
- Desktop ZIP validation: PASS / local ingest: NOT_FOUND / Cloud ingest·owned: UNKNOWN(AI 접근 경로 없음)
- Cloud 건 전부 **HOLD** — 업로드/재인제스트/owned 확인/MCP·CLI 업로드 0.
