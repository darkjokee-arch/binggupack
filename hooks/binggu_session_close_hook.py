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
            except Exception:
                pass
            block = out["rendered"] + (
                "\n\n> ★세션 마무리 감지 — 위 preview 번호로 **사장님이** `SAVE n`/`PAIR ...` 직접 "
                "선택하세요(빙구팩 자동저장 0 · AI 임의저장 금지 · G4).")
            sys.stdout.buffer.write((block + "\n").encode("utf-8"))
            sys.stdout.buffer.flush()
    except Exception:
        pass  # 모든 예외 흡수 — 세션 방해 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
