# INSTALL — OpenBinggu (Personal Track)

> **Track1 공개 1차판(public RC)** — read/dry-run + pack validation + MCP 기본 5도구. "100% 완성판"이 아니며, 모든 사용자 환경 동작을 보장하지 않습니다. 전체 로드맵·범위는 `README.md` 참조.

## Requirements
- Python 3.10+ (표준 라이브러리 위주)
- OS: Windows / macOS / Linux

## Install
```bash
git clone <REPO_URL>
cd openbinggu
python -m venv .venv && . .venv/bin/activate   # 선택
```

## Verify (권장 진입점)
```bash
python scripts/openbinggu_doctor.py --selftest          # 11/11 PASS GATE=GO 기대
python scripts/openbinggu_doctor.py --tree examples/toy_project   # CLEAN 기대
```

## MCP (선택)
`mcp.example.json` 참고. read/dry-run 도구만 노출됩니다(write/apply/push 미노출).

> 공개/업로드 전에는 `python scripts/openbinggu_doctor.py --tree <공개_후보_트리>` 가 CLEAN 이어야 하며, owner 수동 승인 후에만 push/upload 하세요. 자세한 절차는 `README.md`·`docs/` 참고.
