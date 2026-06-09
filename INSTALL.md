# INSTALL — OpenBinggu (Personal Track)

> **Track1 public RC** — v0.1.0-rc1: read/dry-run + pack validation + MCP 5도구 / v0.2.0-rc1: +local persistence(candidate-only, opt-in, write 기본 OFF). "100% 완성판"이 아니며, 모든 사용자 환경 동작을 보장하지 않습니다. 전체 로드맵·범위는 `README.md` 참조.

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
```
> 위 selftest는 **temp OPENBINGGU_HOME** 기준입니다(실제 사용자 홈에 write 0). 실제 저장 기능은 **write 기본 OFF**·명시 opt-in·CLI 전용이며, MCP write 도구는 노출되지 않습니다. candidate-only(`promotion_allowed=0`), confirmed/promote/OpenCrab/Neo4j는 HOLD.

## MCP (선택)
`mcp.example.json` 참고. read/dry-run 도구만 노출됩니다(write/apply/push 미노출).

> 공개/업로드 전에는 `python scripts/openbinggu_doctor.py --tree <공개_후보_트리>` 가 CLEAN 이어야 하며, owner 수동 승인 후에만 push/upload 하세요. 자세한 절차는 `README.md`·`docs/` 참고.
