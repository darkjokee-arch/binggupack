# BingguPack v1.20.0 — Your AI memory, visible and understandable

BingguPack 1.20 turns the consent-first memory engine into a daily product you can inspect from the CLI, MCP, and local Studio.

## 1. 설치

```bash
pip install --upgrade binggupack==1.20.0
```

## 2. 첫 사용

```bash
binggu          # 홈 — 지금 상태와 다음 할 일
binggu inbox    # 통합 검토함
binggu studio   # 로컬 브라우저 UI (읽기 전용)
```

## 3. Daily Console

인자 없이 `binggu`(또는 `binggu home`)를 치면 한 화면에서 봅니다.

- 현재 상태 (활성 기억·자동 수집 의도·원격 저장 의도·대기 승인·검토 예정·장부 무결성·capture/provider)
- 다음 할 일
- **local-only snapshot** (네트워크 fetch 0)
- 안정적 **JSON schema v1** — `binggu home --json` / `binggu inbox --json`

## 4. MCP Front Door

신규 canonical 진입점:

```bash
binggupack-mcp --serve <ROOT>
```

기본 **core profile**(status·recall·why·trace_show·preflight·list·reminders·capture_preview + 승인 기반 save_candidate·pair·deprecate·replace). 고급 전체 표면:

```bash
binggupack-mcp --serve <ROOT> --profile advanced
```

기존 진입점 `openbinggu-mcp-server` 는 계속 동작하며 **기본이 advanced**(하위호환). **제거 일정 없음.**

## 5. Binggu Studio

```bash
binggu studio            # loopback 임시 포트 + 브라우저 자동 열기
binggu studio --no-open  # headless
```

- **Home / Inbox / Memories / Approvals**
- **loopback only** (127.0.0.1) · 실행마다 ephemeral **session-token URL**
- **read-only** (GET/HEAD 만 · mutation endpoint 0 · CORS/외부 asset/network 0)
- owner **command-copy handoff** (기존 CLI 명령 복사만)

## 6. Memory Explorer

- **pagination** (기본 30 · 최대 100 · 무제한 반환 없음)
- active/deprecated·종류·subtype 필터 + 문장 검색
- **lexical-only recall** (의미 검색 설정·캐시를 만들지 않습니다)
- **exact full-ID detail** (id8/fuzzy 없음 · deprecated 도 조회)
- **provenance redaction** (source pointer/hash·raw conversation·nonce 미노출 · evidence 는 safe excerpt)

## 7. Approval Center

- request **effective state** (pending/approved/consuming/consumed/rejected/revoked/expired)
- **integrity-checked review** (operation/payload_digest 일치 검증 · symlink/oversized 거부)
- **timeline** (요청 생성 · 승인 · 거절/취소)
- **receipt** (완료된 작업 · 생성된 기억 링크)
- **local terminal handoff** — `binggu approval show/approve/reject/revoke <request-id>` 복사만

> Studio 는 승인을 실행하지 않습니다. Studio 가 복사한 명령을 owner 가 별도 로컬 터미널에서 직접 실행합니다.

## 8. Compatibility

- schema migration **0**
- dependency 추가 **0** (Python stdlib only)
- 기존 mutation/approval semantics 변경 **0**
- legacy MCP 진입점 `openbinggu-mcp-server` 유지
- 기존 원격 fetch 명령 `binggu hosted inbox` 유지

## 9. Security boundary (정직)

The local Studio preserves BingguPack's existing L1 owner-routing model. It does not create a protected approval authority against an agent that already controls the same shell and filesystem.

- Local TTY 는 L1 owner-routing 이다(암호학적 보증 아님).
- Studio 는 read-only handoff UI 이며 owner approval 을 실행하지 않는다.
- Protected writer/verifier/trust root/detached signer 는 RFC only(미구현).

## 10. Upgrade

- v1.19.0 에서 in-place `pip upgrade` 가능
- schema 변경 없음 · ledger 데이터 형식 불변
- backup 은 일반적으로 권장
- downgrade 시 v1.20 UI/entrypoint 기능만 사라지고 ledger 데이터는 보존된다
