#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""binggu_session_close_hook.py — 세션 마무리 저장 트리거 (UserPromptSubmit · SYNC).

owner 세션 마무리 발화(close_phrases.json 등록 표현 정확 일치)를 감지하면
session_close.process 로 저장 preview(candidate·번호) + 거버넌스 요약을 대화 상단에 자동
주입한다 → owner 가 표를 보고 `SAVE n`/`PAIR ...` 로 직접 선택(저장 0·헌법 candidate-only).

★왜 hook: 세션 마무리 저장 시스템(session_close)은 완비였으나 트리거가 순수 Claude 수동
규약(model_detected_close 신호)이라 2세션째 미동작(hook 0·close_phrases 0) → 이 hook 이
등록 표현 정확 일치로 자동 발동(오늘 강제 회상 배선과 같은 "수동 규약→hook 자동" 교훈).

안전: read-only(session_close 저장 0)·모든 예외 흡수·항상 exit 0·비-마무리 발화 침묵(소음 0)·
close_phrases.json 미등록 → detect is_close False → 침묵. kill switch: <home>/session_close_disabled.
하드코딩 owner 경로 0 — BINGGU_HOME/os.path.expanduser 상대(신규 사용자 대응).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for _p in (REPO, os.path.join(REPO, "scripts")):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)


def _note(stage, exc):
    """예외 삼킴 금지 — 계약(stdout JSON 형식 · 항상 exit 0) 불변인 채 사유 1줄만 stderr 로 남긴다.

    ★왜(2026-07-28 CodeQL py/empty-except 수리): 앵커 stage 가 조용히 실패하면 owner 가 보는
    preview 와 SAVE n 대조 기준이 갈려 오저장/미저장이 무증상으로 일어난다(2026-07-20 이원화 버그
    계열). 예외는 밖으로 던지지 않아 세션은 절대 죽지 않고, 사유만 디버그 채널에 남는다.
    본문은 예외 '타입'만 — 메시지엔 경로·발화 원문이 섞일 수 있어 미출력(유출 0).
    반환: 사유 문자열. 정본 주석: binggu_capture_hook._note."""
    reason = "%s: %s" % (stage, type(exc).__name__)
    try:
        line = "[binggu_session_close_hook] %s\n" % reason
        buf = getattr(sys.stderr, "buffer", None)
        if buf is not None:   # cp949 콘솔에서도 UnicodeEncodeError 0(bytes 직결)
            buf.write(line.encode("utf-8", "replace"))
            buf.flush()
        else:
            sys.stderr.write(line)
    except Exception:
        return reason         # stderr 부재/닫힘 — 반환값으로만 사유 유지
    return reason


def _home():
    env = os.environ.get("BINGGU_HOME")
    return env if env else os.path.join(os.path.expanduser("~"), ".binggupack")


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        return 0
    if (data.get("hook_event_name") or "") != "UserPromptSubmit":
        return 0
    try:
        if os.path.exists(os.path.join(_home(), "session_close_disabled")):
            return 0  # kill switch
    except Exception:
        return 0
    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return 0
    try:
        from binggupack.review.session_close import process
        sid = data.get("session_id")
        out = process({"utterance": prompt, "session_id": sid}, cwd=data.get("cwd"))
        if out.get("is_close") and out.get("rendered"):
            # ★소스 단일화: 마무리 preview 목록(session_id 필터)을 그대로 저장 앵커로 stage.
            #   owner 'SAVE n' 대조 기준을 '눈에 본 preview'로 통일(앵커가 딴 목록이면 오저장 —
            #   이원화 버그). session_id 를 앵커에 심어 save-batch 저장이 동일 세션 목록을 재현.
            #   저장 0(hash 만)·best-effort(실패해도 preview 표시엔 무영향).
            try:
                pv = (out.get("summary") or {}).get("preview") or {}
                items = pv.get("items") or []
                if items:
                    from binggu_save_batch import stage_batch_anchor
                    stage_batch_anchor(items, session_id=sid)
            except Exception as e:
                # 앵커 실패해도 preview 표시는 계속(기존 best-effort 계약 유지) — 다만 삼키지 않고
                # 사유를 남긴다. 무증상이면 owner 의 SAVE n 이 조용히 빗나간다.
                _note("SAVE 앵커 stage 실패(preview 표시는 계속)", e)
            # ★T0 그래프편입 자동관측(헌법 v2) — opt-in ON 일 때만. 회상 노드가 '회상 이후 새 엣지'로
            #   편입됐으면 used 자동 도장(사람 도장 불요). ledger read-only(sibling recall_trace 만
            #   append)·best-effort·예외 흡수. 기본 OFF(auto_observe_enabled) — dry-run 확인 후 owner 가
            #   켠다(첫 대량도장 방지). session_close(표시)는 read-only 유지 — write 는 이 hook 레벨만.
            observe_note = ""
            try:
                from binggupack.pack import recall_trace as _RT
                _h = _home()
                if _RT.auto_observe_enabled(_h):
                    from datetime import datetime, timezone
                    _ao = _RT.auto_observe_adoption(
                        datetime.now(timezone.utc).isoformat(),
                        home=_h, ledger_path=os.path.join(_h, "ledger.sqlite"))
                    _n = _ao.get("observed", 0)
                    if _n:
                        observe_note = ("\n\n> ✅ 회상 %d건이 새 엣지로 편입돼 자동 채택(used) 관측됨 "
                                        "(T0 자율 · owner 도장 불요 · 운영 ledger 불변)." % _n)
            except Exception as e:
                # 자동관측 실패해도 preview 표시는 계속(opt-in 부가 기능) — 사유만 남긴다.
                _note("T0 그래프편입 자동관측 실패(preview 표시는 계속)", e)
            block = out["rendered"] + observe_note + (
                "\n\n> ★세션 마무리 감지 — 위 preview 번호로 **사장님이** `SAVE n`/`PAIR ...` 직접 "
                "선택하세요(빙구팩 자동저장 0 · AI 임의저장 금지 · G4).")
            # ★owner 표시 배선(2026-07-22 owner 실측 재발 "미스후보 12건 나한테는 안 보인다"):
            #   PR#103 은 systemMessage 필드로 owner 표시를 시도했으나 이 Claude Code 환경은
            #   UserPromptSubmit 의 systemMessage 를 owner 화면에 표시하지 않는다(additionalContext 는
            #   Claude 에 정상 주입 실측). 따라서 확실한 경로 = additionalContext + Claude 렌더 —
            #   hook 이 Claude 에게 "이 표를 owner 응답에 전문 렌더하라"고 명시 지시한다(Claude 가
            #   놓쳐도 방어). systemMessage 는 작동하는 환경/버전 대비 병행 유지. 저장/도장은 사람만(G4 불변).
            claude_directive = (
                "[Claude 필수 — 세션 마무리 preview 렌더] 아래 저장/회상 후보 표(번호+원문)를 "
                "**네 응답에 그대로 옮겨** 사장님이 보게 하라. 이 additionalContext 는 너에게만 보이고 "
                "사장님 화면엔 안 뜬다(이 환경 systemMessage 미표시). '위 preview 참조'·'hook 에 있음'·"
                "요약 금지 — 저장 후보·회상 후보 전문 렌더 필수. 저장/도장(SAVE/히트/미스)은 사장님만.\n\n"
                "────────────────\n\n")
            payload = json.dumps({
                "systemMessage": block,
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": claude_directive + block,
                },
            }, ensure_ascii=False)
            sys.stdout.buffer.write((payload + "\n").encode("utf-8"))
            sys.stdout.buffer.flush()
    except Exception:
        pass  # 모든 예외 흡수 — 세션 방해 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
