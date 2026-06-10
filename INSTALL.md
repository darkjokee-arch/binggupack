# INSTALL — BingguPack (Personal Track)

> OpenBinggu is the legacy/internal codename for BingguPack.

> **Track1 public RC** — v0.1.0-rc1: read/dry-run + pack validation + MCP 5도구 / v0.2.0-rc1: +local persistence(candidate-only, opt-in, write 기본 OFF) / v0.3.0-rc1: +manual one-shot capture(read-only). "100% 완성판"이 아니며, 모든 사용자 환경 동작을 보장하지 않습니다. 전체 로드맵·범위는 `README.md` 참조.

## Requirements
- Python 3.10+ (표준 라이브러리 위주)
- OS: Windows / macOS / Linux

## Install
```bash
git clone https://github.com/darkjokee-arch/binggupack.git
cd binggupack
python -m venv .venv && . .venv/bin/activate   # 선택
```

## Verify (권장 진입점)
```bash
python scripts/openbinggu_doctor.py --selftest          # 11/11 PASS GATE=GO 기대
python scripts/openbinggu_doctor.py --tree examples/toy_project   # CLEAN 기대
```

## Local persistence selftest (v0.2.0-rc1, opt-in 기능 검증)
```bash
python scripts/openbinggu_phase2_local_persistence_selftest.py   # 11/11 PASS GATE=GO 기대
python scripts/openbinggu_phase2_staging_reread_e2e.py           # 10/10 PASS GATE=GO 기대(read-only 재독)
python scripts/openbinggu_batch_pack_loader.py --selftest        # 10/10 PASS GATE=GO 기대(batch pack→staging apply→rollback)
python scripts/openbinggu_promotion_preview.py --selftest        # 12/12 PASS GATE=GO 기대(v0.4.0-rc1 read-only promotion preview)
```
> 위 selftest는 **temp OPENBINGGU_HOME** 기준입니다(실제 사용자 홈에 write 0). 실제 저장 기능은 **write 기본 OFF**·명시 opt-in·CLI 전용이며, MCP write 도구는 노출되지 않습니다. candidate-only(`promotion_allowed=0`), confirmed/promote/OpenCrab/Neo4j는 HOLD.

## Manual capture selftest (v0.3.0-rc1, read-only)
```bash
python scripts/openbinggu_phase6_manual_capture_selftest.py   # 10/10 PASS GATE=GO 기대
```
> **synthetic / temp / read-only** 기준. 사용자가 명시 지정한 경로만 capture(allowlist only, denylist 우선), raw 저장 0·source pointer 공개 미포함. **write opt-in 없으면 staging write 0**, **hook/daemon은 NOT_STARTED**(설치/실행 0). reviewer/confirmed preview selftest(Phase 4)는 의존성 검토 중으로 이번 RC에 미포함.

## MCP (선택)
`mcp.example.json` 참고. read/dry-run 도구만 노출됩니다(write/apply/push 미노출).

> 공개/업로드 전에는 `python scripts/openbinggu_doctor.py --tree <공개_후보_트리>` 가 CLEAN 이어야 하며, owner 수동 승인 후에만 push/upload 하세요. 자세한 절차는 `README.md`·`docs/` 참고.
