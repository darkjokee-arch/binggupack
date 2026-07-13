#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Interactive save gate (Lane C).

기존 confirm-phrase safety model을 **유지**하는 보조 UX. interactive 는 후보 선택과
confirm phrase 구성을 도울 뿐, 마지막 human confirmation 과 실제 저장 게이트는 기존
경로를 그대로 쓴다. interactive 자체는 ledger 에 직접 write 하지 않는다.

불변식:
- TTY 가 아니면 fail-closed (CI·pipe·AI tool_use 에서 비활성 → 기존 explicit 방식 강제).
- `--yes`/`--force`/자동승인 없음. 사람이 phrase 를 직접 다시 타이핑해야 통과.
- AI actual save 불가. `save_candidate(dry_run=false)` 의 G4_no_auto 차단과 독립적으로,
  이 모듈은 어떤 경로로도 게이트를 우회하지 않는다.
- stdlib only (표준 입력). curses/inquirer 등은 미사용.

usage:
  python -m binggupack.cli.interactive_save            # TTY 에서 대화형
  python -m binggupack.cli.interactive_save --selftest # 비-TTY 검증 (저장 0)
"""
import argparse
import sys

try:
    from binggupack.workspace.platform import invocation_prefix
except Exception:  # pragma: no cover — 구버전/부분설치 폴백
    def invocation_prefix(argv0=None):
        return "python binggu.py"

_VALID_ACTIONS = ("SAVE", "DEPRECATE", "REPLACE", "ACCEPT", "UNACCEPT")


def build_confirm_phrase(action, indices, id8=None, replacement=None):
    """선택값으로 기존 게이트가 요구하는 confirm phrase 를 구성. 순수 함수(저장 0)."""
    act = str(action).strip().upper()
    if act not in _VALID_ACTIONS:
        raise ValueError("unknown action: %r" % action)
    if not indices:
        raise ValueError("indices required")
    idx = ",".join(str(int(i)) for i in indices)
    if act == "SAVE":
        return "SAVE %s" % idx
    # 단건 변경 액션은 행번호 + id8 필수 (목록 바뀌면 자동 차단 — 기존 정책 동일)
    n = int(indices[0])
    if act in ("DEPRECATE", "ACCEPT", "UNACCEPT"):
        if not id8:
            raise ValueError("%s requires id8" % act)
        return "%s %d %s" % (act, n, id8)
    if act == "REPLACE":
        if not (id8 and replacement):
            raise ValueError("REPLACE requires id8 and replacement")
        return "REPLACE %d %s WITH %s" % (n, id8, replacement)
    raise ValueError("unreachable")


def _is_tty():
    return bool(getattr(sys.stdin, "isatty", lambda: False)() and
                getattr(sys.stdout, "isatty", lambda: False)())


def _require_tty():
    if not _is_tty():
        print("[interactive] TTY 환경에서만 동작합니다(fail-closed).")
        print("  비대화형(CI/pipe/AI)에서는 기존 explicit confirm 방식을 쓰세요:")
        print('  %s save "<text>" --preview-id <id> --pick 1 --confirm "SAVE 1"'
              % invocation_prefix())
        sys.exit(2)


def interactive_main():
    """TTY 대화형 흐름. 실제 저장은 기존 게이트 CLI 로 위임(우회 0)."""
    _require_tty()
    print("== BingguPack interactive save gate ==")
    print("후보를 고르고 action 을 정하면 confirm phrase 를 구성합니다.")
    print("실제 저장은 마지막에 사람이 phrase 를 직접 입력하고, 기존 게이트 명령으로 실행됩니다.\n")

    raw = input("저장할 후보 번호(쉼표): ").strip()
    indices = [int(x) for x in raw.replace(" ", "").split(",") if x]
    action = input("action [SAVE/DEPRECATE/REPLACE/ACCEPT/UNACCEPT] (기본 SAVE): ").strip() or "SAVE"
    id8 = None
    replacement = None
    if action.upper() != "SAVE":
        id8 = input("id8 (목록의 id 칼럼 8자): ").strip()
    if action.upper() == "REPLACE":
        replacement = input("수정 문장: ").strip()

    phrase = build_confirm_phrase(action, indices, id8, replacement)
    print("\n구성된 confirm phrase:\n  %s" % phrase)
    typed = input('승인하려면 위 phrase 를 그대로 다시 입력(취소는 빈 입력): ').strip()
    if typed != phrase:
        print("불일치 — 취소되었습니다. 저장 0.")
        return 0

    print("\n[다음 단계] 마지막 human confirmation 은 기존 게이트에서 유지됩니다.")
    print("아래 명령으로 실제 저장하세요(interactive 는 ledger 에 직접 쓰지 않습니다):")
    print('  %s save "<같은 텍스트>" --preview-id <id> --pick %s --confirm "%s"'
          % (invocation_prefix(), ",".join(str(i) for i in indices), phrase))
    return 0


def selftest():
    """비-TTY 검증 (저장 0). build_confirm_phrase 정확성 + 안전 불변식."""
    cases = [
        (("save", [1, 2]), "SAVE 1,2"),
        (("SAVE", [3]), "SAVE 3"),
        (("deprecate", [3], "a1b2c3d4"), "DEPRECATE 3 a1b2c3d4"),
        (("accept", [5], "deadbeef"), "ACCEPT 5 deadbeef"),
        (("replace", [2], "abcd1234", "fixed sentence"), "REPLACE 2 abcd1234 WITH fixed sentence"),
    ]
    n = 0
    for args, expect in cases:
        got = build_confirm_phrase(*args)
        assert got == expect, "phrase mismatch: %r != %r" % (got, expect)
        n += 1
    # 잘못된 입력은 거부
    for bad in [("YES", [1]), ("save", []), ("deprecate", [1])]:  # 자동승인/빈선택/id8 누락
        try:
            build_confirm_phrase(*bad)
            raise AssertionError("should have rejected: %r" % (bad,))
        except ValueError:
            n += 1
    print("GATE: GO (interactive_save selftest %d/%d) · ledger_write=0 · G4_bypass=0 · tty_fail_closed=ready" % (n, n))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="BingguPack interactive save gate (보조 UX)")
    ap.add_argument("--selftest", action="store_true", help="비-TTY 검증(저장 0)")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    return interactive_main()


if __name__ == "__main__":
    sys.exit(main())
