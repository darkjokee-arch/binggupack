# BingguPack v1.19.0 — Consent-first, exact-bound AI memory (Stable)

동의 우선(consent-first)·증거 기반 개인 기억/컨텍스트 팩. v1.19.0rc1 을 실경로 검증 완료 후
stable 로 승격한 첫 정식 릴리스입니다.

## 설치

```
pip install binggupack        # stable (--pre 불필요)
binggu demo --non-interactive # 60초 오프라인 체험(네트워크·API 키 0)
```

## 이번 릴리스

v1.19.0 은 **v1.19.0rc1 의 version-only promotion** 입니다. RC 이후 production code 변경이
없으며(pyproject / `binggupack/__about__.py` 의 version literal · CHANGELOG · 이 release notes 만 변경),
RC artifact 와 production 모듈은 version literal 을 제외하면 byte-equivalent 입니다.

### 승격 근거 — 실경로 mobile/web→PC canary 통과

배포된 실제 경로(claude.ai 커넥터 → hosted 큐 → PC auto_pull/staging → owner 로컬 승인 → exact-bound
commit)에서 synthetic fixture 1건으로 전 구간을 라이브 검증했습니다.

- `direct_write_before_approval = 0` — 승인 전 active memory 저장 0
- PENDING approval request 정확히 1개 · exact bundle membership
- **owner 물리 승인만 통과** — 비대화형/AI/pipe/`!` 는 `isatty` fail-closed 로 전부 차단(exit 2·no-mint),
  대화형 TTY 사람 승인(`cli_tty`)만 mint
- exact-bound commit `write = 1` · retry `write = 0`(one-time consume) · original receipt 반환
- source 원문 자동삭제 0(archive 보존) · audit chain INTACT · unrelated mutation 0

## 주요 기능 (v1.19.0rc1 에서 봉인, 그대로 승계)

- `binggu demo` — 60초 오프라인 체험
- Trusted approval event — owner 로컬 승인으로 MCP mutation 정확히 1회(exact operation/payload/ledger/
  version 바인딩 · one-time consume)
- hosted intent → local approval → exactly-once crash-atomic bundle commit(원문 자동삭제 0 · direct hosted
  write 0)
- 승인 기원 계약(env·비대화형·confirm 문구·actor 라벨은 권한이 아님)

## 정직한 보안 경계 (변경 없음)

- 로컬 TTY 는 L1 owner routing 이며, shell/filesystem 병재 에이전트에는 하드 승인 권한이 아닙니다.
- protected writer / verifier / trust root / detached signer 는 RFC only(미구현).
- root/admin compromise 방어를 주장하지 않습니다.

전체 변경 근거·회귀 테스트는 `CHANGELOG.md` 의 `[1.19.0]` / `[1.19.0rc1]` 및 P1-* 섹션을 참조하세요.
