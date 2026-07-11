# BingguPack v1.19.0-rc1 — Consent-first, exact-bound AI memory

> **Release Candidate.** PEP440 version `1.19.0rc1` · future tag `v1.19.0rc1` (no hyphen).
> 안정(stable) 설치는 이 RC 를 받지 않는다 — 아래 pre-release 설치를 참고.

## 1. 한 문장

**AI 는 기억 후보를 만들 수는 있지만, 활성 기억은 owner 의 exact-bound 승인 없이 생성되지 않는다.**

## 2. 대표 사용자 경험

```bash
pip install --pre binggupack==1.19.0rc1
binggu demo          # 60초 오프라인 체험 (네트워크·API 키 0)
```

`binggu demo` 는 격리 임시 장부에서 **Candidate → Review → Commit → Recall → Explain** 전 과정을
새 프로세스 회상과 근거(provenance)까지 오프라인으로 보여준다. 운영 장부는 구조적으로 미접촉이다.

## 3. 주요 변화

- **60초 데모** — 설치 직후 오프라인으로 전체 흐름 체험
- **Candidate → Review → Commit → Recall → Explain** 멘탈 모델
- **Trusted approval event** — owner 로컬 승인으로 MCP mutation 정확히 1회
- **Hosted intent → local approval → exactly-once commit** — 폰/웹은 저장 **의도만** 전달,
  PC 가 선택 묶음 전체를 exact-bound `--approval-id` 로 **crash-atomic** 커밋
- **Provenance / receipt** — 확정 기억마다 근거·1회용 소비 영수증
- **Python 3.10 / 3.12 / 3.13 + 3 OS(Linux·macOS·Windows) 검증**

## 4. Breaking / behavior changes (반드시 확인)

- **confirm 문구만으로 비대화형 write 불가** — confirm 은 형식 검증일 뿐이다.
- **`actor=human` 으로 권한 승격 불가** — actor 라벨은 감사 메타일 뿐 권한이 아니다.
- **폰/웹 confirm 은 pending approval request 만 생성** — 실제 저장은 owner 가 PC 에서 승인해야 한다.
- **기존 자동화는 approval_id 흐름으로 변경 필요** — 비대화형 owner 경로는 `--approval-id` 를 쓴다.
- **direct hosted save 는 제거됨** — 유일 저장 경로는 로컬 exact-bound `hosted_bundle` 승인이다.
- **`BINGGU_TRUSTED_CLI` 백도어 제거 · `BINGGU_STRICT_HUMAN_GATE` deprecated no-op.**

## 5. Honest security boundary (정직)

- MCP-only tool surface(웹/앱 커넥터·잠긴 에이전트)로 **격리된 배포**에서 L1 경계가 유효할 수 있다.
- **Shell/filesystem 을 함께 가진 에이전트가 있는 환경에서는 승인 저장소(approval store) 자체가
  보호되지 않는다.** 그 환경에서 P1-A/P1-B 는 "자동저장 방지 + 비대화형 owner 경로 + intent-routing"
  이지 하드 통제가 아니다.
- **Protected writer 는 RFC 이며 production 미구현**(Track B · `docs/BINGGUPACK_PROTECTED_WRITER_RFC.md`).
  verifier / trust root / detached signer / trusted display 도 전부 설계 문서이며 코드 0.
- **root/admin compromise 는 방어 대상이 아니다.** "human proof", "tamper-proof", "완전 보안" 같은
  표현은 쓰지 않는다. 장부 무결성은 **손상·변조 감지**(hash chain + Merkle)이지 암호학적 봉인이 아니다.
- **알려진 한계(비-blocker):** COMMIT 직후~archive 직전 사이에 **로컬 파일시스템 write 권한을 가진
  행위자**가 staging 원문을 변조하면, 디스크의 archive provenance 파일(`_archive/*.processed.json`)이
  변조본을 반영할 수 있다. 그러나 **장부(recall 의 진실 원천)는 면역**이며 archive 파일은 recall/consume
  에서 다시 읽히지 않는다(코스메틱 · SECURITY.md 위협모델상 전권 로컬 행위자는 out of scope).

## 6. Backup / rollback

- **업그레이드 전 백업 권장:** `binggu backup` (운영 장부를 `_backup/ledger_<ts>.sqlite` 로 스냅샷,
  운영 write 0). 자세한 절차는 [업그레이드 가이드](BINGGUPACK_V118_TO_V119_UPGRADE.md).
- **마이그레이션은 additive(비파괴):** v1.18.3(schema v1) → rc1(schema v2) 는 `approval_requests`·
  `approval_consumptions` 두 테이블 추가 + `user_version` 1→2 뿐이다. DROP/RENAME/retype 0, 데이터 손실 0,
  재실행 멱등. (실측: 채워진 v1 장부 업그레이드 후 행 수·문장·audit chain 불변.)
- **In-place downgrade(rc1 → 1.18.3)는 스키마상 안전하나 보안 회귀다** — 구 코드는 추가 테이블을 무시하고
  데이터를 보존하지만 **trusted-approval-event 강제 계층이 사라져** 구 confirm-only 게이트만 남는다.
  운영 장부에서 실험하지 말 것. 데이터 되돌림이 필요하면 restore 기반 롤백만 안내한다:
  `binggu restore <backup> --confirm "RESTORE <file>"`.

## 7. RC 설치와 피드백

- **Pre-release 설치:** `pip install --pre binggupack==1.19.0rc1`
- **안정 사용자는 기본 `pip install binggupack` 로 이 RC 를 받지 않는다**(PEP440 pre-release 규칙).
  GitHub Release 는 pre-release 로 표시한다.
- **문제 보고 시 raw ledger / 원문을 업로드하지 말 것.** reason code 와 synthetic reproduction 을 사용한다.
- **owner 확인 필요(배포 전):** GitHub → Settings → Environments → `pypi` 의 required-reviewer 보호 규칙이
  켜져 있는지 확인(publish 전 사람 승인 게이트). Trusted Publisher 등록은 owner 수동 선행.

---

### 부록 A — 태그/버전 규칙 (publish fail-closed)

- publish 는 **정확한 버전 태그 ref** 에서만 실행된다. branch/main dispatch 는 업로드 전 하드 차단.
- **RC 태그는 하이픈 없이 `v1.19.0rc1`** (PEP440 canonical). 하이픈 태그 `v1.19.0-rc1` 은 fail-closed.
- tag == `pyproject [project].version` == `binggupack.__about__.__version__` (3중 일치) + tag commit == HEAD +
  clean tree + 사설경로 스캔 통과라야 업로드에 도달한다. 정적 검증: `scripts/publish_workflow_selftest.py`.

### 부록 B — 별도 owner 처리 항목 (이 RC 밖)

- **historical exposure:** 이전 공개 패키지(1.18.3, 이미 PyPI 공개)의 소스에 owner 절대경로/사설
  프로젝트 토큰이 **path metadata** 로 포함돼 있었다(secret/key/token 아님). 이번 RC 트리·산출물은
  `private_path_scan` 으로 정리했으나, 이미 공개된 1.18.3 의 이력 처리(예: yank 여부)는 별도 owner 결정이다.
- **deploy-worker.yml(DEP-1):** Cloudflare Worker 배포 워크플로의 `workflow_dispatch` 는 브랜치 코드도
  프로덕션 워커로 배포 가능하다(현재 medium · 이번 PyPI RC 범위 밖). deploy job 에 `if: github.ref ==
  'refs/heads/main'` 가드 추가를 권장 — owner 별도 처리.
- **synthetic PII 테스트 fixture:** PII-리댁션 기능을 검증하는 7개 `*_selftest.py` 파일에 **가짜**
  RRN/전화번호 형태 문자열(총 14곳)이 테스트 데이터로 들어 있다(예: capture buffer/classifier/cli/session
  selftest · save_gate characterization). owner 실제 PII·secret 아님(리댁션이 동작하는지 확인하기 위한
  합성 입력)이며 1.18.3 에도 존재한 pre-existing 항목이다. 프로젝트 자체 tree-scan 게이트는 test-fixture
  PII 를 ignore 하므로 repo 스캔은 CLEAN 이나, 빌드 산출물을 fresh 스캔하면 표면화된다. 배포 산출물에서도
  이 합성 fixture 를 제거할지는 별도 owner 결정(리댁션 selftest 무결성 유지 필요).
