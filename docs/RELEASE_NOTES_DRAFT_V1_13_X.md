# Release Notes (DRAFT) — BingguPack v1.13.x

> 상태: **초안(DRAFT)**. GitHub release 는 생성하지 않았다(owner 영역). 아래 텍스트는 release 본문에 붙여 쓸 초안이다.
> baseline `HEAD == origin/main == 407656c` · version `1.13.0`.

---

## 사용자용 한 줄 요약

빙구팩 v1.13.0 — 내 학습과 기존 규칙이 충돌하면 양쪽을 보여주고 **내가** 선택. 규칙 변경은 사람 손, 안전 규칙은 빙구팩이 못 바꿈(자기진화 거버넌스). 여기에 더해 **Claude Code MCP 저장 도구의 크래시(연결 끊김)를 수정**했다.

---

## GitHub Release 본문 초안 (복사용 — 생성 금지, 텍스트만)

```markdown
# BingguPack v1.13.0 — 자기진화 거버넌스 🧭

> 🔒 로컬 우선 · 자동 저장 없음 · 내가 고른 것만 저장 · MIT License
> 설치: `git clone` (PyPI publish 미지원 — INSTALL.md 참고)

## 한눈에
- **자기진화 거버넌스**: 빙구팩 학습과 기존 규칙(강제조항)이 충돌하면 — 빙구팩은 제안·기록만 하고, 규칙 변경은 사람이 한다. 안전 규칙은 빙구팩이 못 바꾼다(self-modifying write 0).
- **MCP 저장 크래시 수정**: Claude Code MCP `save_candidate` 가 실 저장 단계에서 서버가 죽어 "Connection closed" 가 뜨던 문제 수정. 이제 같은 입력에서 정상 응답.
- **저장 경로 보장 재확인**: MCP 저장 도구는 임시(temp)까지만. 운영 노트로의 영구 저장은 사람이 직접 고를 때만(AI/MCP 단독 영구 저장 0).

## Added (거버넌스)
- 1단 대비(`binggu_contrast_protocol.py`): 학습↔규칙 충돌 → 중립 대비표 · 원문 본문 봉인 · 사람 선택 · 자동결정 0.
- 2단 적중률(`binggu_hit_stats.py` 확장): 비인과 불변식 · domain 분리 · signal_only.
- 2단 무결성(`binggu_merkle_anchor.py`): atomic 봉인 · 외부 raw 재계산 · 누락 fail-closed.
- 2단 정책(`binggu_policy.py` + `policies/binggu_policy.json`): REQUIRED_IMMUTABLE 화이트리스트 · pin fail-closed.
- 3단 분리(`binggu_hit_export.py`): capability-removal · realpath 물리가드 · raw export만(규칙 write 0).
- 세션 마무리(`binggu_session_close.py`): 모델 의미감지 · 사용자 opt-in · 저장 preview(저장 0).
- `docs/BINGGUPACK_GOVERNANCE_DESIGN.md` 신규.

## Fixed
- **MCP `save_candidate` 실 write 크래시**(`407656c`): snapshot 임시 폴더(`snap_dir`) 미생성으로 `FileNotFoundError` → stdio 루프 종료 → 프로세스 사망("Connection closed"). 핸들러 1줄로 폴더 보장. dry-run/조기 BLOCK 경로는 영향 없었고 실 write 경로만 죽던 패턴. write core·gate·G4·actor·token·ledger path 미접촉, version 불변.

## Safety
- 5개 가드 코드 강제·통과: self-modifying write 0 · 대비표 원문봉인 · 적중률 비인과 · Merkle atomic · 정책 REQUIRED_IMMUTABLE.
- MCP 경유 저장은 actor=reader 하드 오버라이드 → `G4_no_auto` 로 영구 저장 항상 BLOCK(`ledger=temp_only`). 영구 저장은 사람(actor=human) 직접 선택만.

## Verified
- version SSOT 1.13.0 일치(`__about__` · pyproject · README · CHANGELOG · 빌드 메타) — version_consistency_selftest 3/3 GO.
- 격리 venv 빌드: `binggupack-1.13.0` wheel/sdist 빌드 성공 · `twine check` PASSED · 설치 후 `import OK version=1.13.0`.
- smoke 10/10 PASS(production_write 0 · actual_api_call 0 · G4_no_auto confirmed · real_home_changed 0).
- 운영 ledger 마이그레이션 무손상(291노드 · verify_tail_state/chain True) — governance_write_zero.

## Known issue
- 회귀 묶음(`binggu_publish_run_all_selftests.py`)은 23/24 PASS. 24번째 tree-scan 게이트가 `verdict=BLOCK` 으로 떨어지는데, 이는 PII 탐지기가 **자신의 selftest 합성 fixture**(전화/주민번호 형태의 합성 테스트 패턴)를 정상 검출하기 때문이다. 실제 개인정보/시크릿 유출이 아니며 release blocker 아님(상세: `docs/RELEASE_READINESS_V1_13_X.md` §4).

## 설치
```bash
git clone https://github.com/darkjokee-arch/binggupack
cd binggupack
python scripts/smoke_test.py --home ./_binggu_test_home
python scripts/install_claude_mcp.py --sandbox --apply
```

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

---

## 릴리스 전 owner 체크리스트 (참고 — 본 lane 미수행)

- [ ] DRIFT-1: `INSTALL.md` v1.12.0 → v1.13.0 표기 갱신(RELEASE_READINESS §2 제안 patch)
- [ ] DRIFT-2: `pyproject.toml [project.urls] Release` v1.10.0 → v1.13.0
- [ ] (선택) tree-scan 게이트 fixture 제외 조정으로 24/24 (코드 변경)
- [ ] `git tag v1.13.0` / push (owner)
- [ ] GitHub release 생성 (owner) — 위 본문 초안 사용
- [ ] (보류) PyPI publish — 현재 git clone 설치만 지원
