# Upgrade guide — BingguPack v1.18.3 → v1.19.0rc1

> RC(pre-release). 안정 사용자는 기본 `pip install` 로 이 버전을 받지 않는다.
> 설치: `pip install --pre binggupack==1.19.0rc1`.

## 요약

v1.18.3 → v1.19.0rc1 업그레이드는 **데이터 무손실 · additive 마이그레이션**이다. 단 하나의 의도된
breaking 은 **보안 수정**이다: 구 MCP confirm-only 자동 write 경로가 완전히 fail-closed 로 바뀐다.

## 0. 업그레이드 전 (권장)

```bash
binggu backup          # 운영 장부를 _backup/ledger_<ts>.sqlite 로 스냅샷 (운영 write 0)
binggu export <dir>    # (선택) 이식용 내보내기
python -m binggupack doctor
```

## 1. 스키마 마이그레이션 — additive · 멱등 · 무손실

- `SCHEMA_VERSION` 1 → 2. 추가되는 것: `approval_requests`, `approval_consumptions` 두 테이블
  (`CREATE TABLE IF NOT EXISTS`) + 필요한 경우 누락 컬럼 `ALTER TABLE ADD COLUMN`(비파괴 backfill).
- **DROP / RENAME / retype 0.** 기존 nodes / edges / candidate / hit_events / owner_acceptances /
  recall_traces 는 전부 그대로 보존된다.
- 첫 rc1 open 시 `audit_meta['ledger_id']` 를 1회 mint 한다(v1.18.3 엔 없었음). 무해 — 1.18.3 에는
  ledger_id 에 묶인 승인이 애초에 없었다.
- **멱등:** apply_schema 를 반복 실행해도 안전하고, 중단 후 재실행도 가능하다.
- 검증: `python scripts/binggu_schema.py --selftest` (레거시 backfill · 멱등 재적용 · user_version bump).

업그레이드 후:

```bash
python -m binggupack doctor     # 스키마/무결성 점검
binggu list                     # 기존 활성 기억 그대로 조회
binggu recall "<질의>"          # 회상 동작 확인
```

## 2. 동작 변화 (MCP 저장 흐름)

> ⚠ superseded by 저장 게이트 개정(2026-07-12 사장님 룰 · CHANGELOG [Unreleased]): 저장(save/pair/hosted pull)의
> 사람 증명은 "preview + 사람의 save n 입력" 단일 원칙으로 바뀌었고 hosted 저장의 `--approval-id` 절차는 제거됐다.
> 아래 서술은 v1.19 당시 기준 기록이다(MCP fail-closed·비저장 mutation approval 은 현행 유지).

- 업그레이드 후 **MCP mutation 도구는 기본 fail-closed** 다(`write_available: false`,
  `reason: trusted_approval_event_required`). 실제 저장이 되려면:
  1. owner 가 승인 provider 를 구성(`~/.binggupack/trusted_approval.json` 등)하고,
  2. 각 요청을 `binggu approval approve <req-id>` 로 승인해야 한다.
- **owner 로컬 CLI / 키보드 `SAVE n` 앵커 write 는 영향 없음** — 그대로 동작한다.
- **기존 confirm-only 자동화는 더 이상 자동 저장하지 않는다.** confirm 문구 재현만으로는
  `actor=human` 승격이 되지 않는다(P0 봉인). 자동화가 필요하면 비대화형 owner 경로
  `--approval-id`(exact-bound) 로 전환한다.
- **폰/웹/ChatGPT 저장 채널:** 저장 **의도만** 전달된다. PC 가 선택 묶음 전체를 exact-bound
  `--approval-id` 로 승인·커밋(exactly-once · crash-atomic)해야 활성 기억이 된다.
- 기존 hosted pending / outbox intent JSON 은 wire 포맷 불변으로 업그레이드 후에도 drain 가능하다.

## 3. Downgrade (되돌리기) — 스키마 안전, 보안 회귀

- **In-place downgrade(rc1 → 1.18.3)는 스키마상 안전**하다: 구 코드는 추가된 approval 테이블을
  무시(drop 하지 않음)하고 `user_version` 을 2 로 둔 채 모든 데이터를 보존한다. read-only
  `doctor` / `list` / `recall` 은 정상 동작한다.
- **그러나 downgrade 는 보안 회귀다** — trusted-approval-event 강제 계층과 P0/P1-A 의 MCP write
  봉인이 사라지고 구 confirm-only 게이트만 남는다. 코드를 되돌리는 것은 취약점 재도입이다.
- 지원 롤백 경로: (a) 코드만 되돌림 → `pip install binggupack==1.18.3` (장부는 계속 동작),
  (b) 데이터 되돌림이 필요하면 restore 기반만 안내 —
  `binggu restore <backup> --confirm "RESTORE <file>"`(sqlite+nodes 검증 · 현재 장부를
  `_backup/pre_restore_<ts>.sqlite` 로 자동 스냅샷). **운영 장부에서 실험하지 말 것.**

## 4. 검증(자체)

이 업그레이드 경로는 synthetic temp 홈에서 E2E 로 검증된다(운영 홈 미접촉):
채워진 v1.18.3 장부 build → rc1 wheel in-place 업그레이드 → doctor/verify → 기존 데이터 조회 →
approval 요청·승인 → exactly-once mutation → hosted bundle → retry second-write 0 →
backup/export 재실행 → 스냅샷 비교(active node/evidence 손실 0 · audit chain INTACT · migration 멱등).
