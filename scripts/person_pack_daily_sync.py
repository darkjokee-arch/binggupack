# -*- coding: utf-8 -*-
"""person_pack_daily_sync — 개인 온톨로지 팩 일 1회 자동 갱신 orchestrator.

각 사용자의 ~/.claude/memory 자산을 재조립(person_pack_assemble) → 변경 버킷만
제자리 교체 업로드(person_pack_split_upload --daily·안 바뀌면 NO_CHANGE·네트워크 0).
스케줄러(pythonw·일 1회)에서 호출한다. pythonw 는 콘솔이 없어 stdout 이 None →
모든 출력을 로그 파일로 리다이렉트해야 print 가 안전.
로그: <home>/.claude/logs/person_daily_sync.log
"""
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
LOG = Path.home() / ".claude" / "logs" / "person_daily_sync.log"


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    old = sys.stdout, sys.stderr
    rc = 1
    with open(LOG, "a", encoding="utf-8") as f:
        sys.stdout = sys.stderr = f
        try:
            print("\n=== person_pack_daily_sync %s ===" % time.strftime("%Y-%m-%dT%H:%M:%S"))
            import person_pack_assemble as ASM
            import person_pack_split_upload as PSU
            print("[1/2] assemble → person_split_sources ...")
            ASM.main()
            print("[2/2] split_upload --daily (변경 버킷만) ...")
            rc = PSU.main(daily=True)
            print("rc=%s" % rc)
        except Exception:
            traceback.print_exc()
            rc = 1
        finally:
            sys.stdout, sys.stderr = old
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
