# -*- coding: utf-8 -*-
"""binggu demo 본체 — binggu.py 에서 이관(구조 정리·동작 불변).

cmd_demo(진입점·격리 가드 _operating_home/_same_path/_canonical_path)는 binggu.py 에 잔류하고,
이 모듈의 _demo_body 를 lazy import 로 호출한다. 운영 홈 미접촉·격리 데모 홈 전용(actor=reader·
승인 우회 아님·운영 장부 write 0). capture_preview·save_selected 백본은 binggu.py 잔류분을 재사용.
"""
import os
import sys

# DEMO_SCENARIO 는 tests/test_demo.py 와 공유하는 SSOT 라 binggu.py 에 잔류(test_demo.py:22 from import).
# capture_preview·save_selected 는 binggu 모듈 **속성**으로 런타임 참조한다 — 원본(_demo_body 가
# binggu.py 전역을 참조)의 동작을 정확히 복제하고, test 가 binggu.capture_preview 를 monkeypatch 하는
# 계약을 보존하기 위함(from import 로 복사하면 patch 가 안 먹힌다).
import binggu  # noqa: E402  (binggu 완전 로드 후 lazy 진입 — 순환 차단)
from binggu import DEMO_SCENARIO  # noqa: E402  공유 SSOT 상수(patch 대상 아님)


def _demo_body(a, demo_home, keep, created_tmp, op_home):
    """cmd_demo 본체(후보→승인→저장→새 프로세스 회상→근거). 정리/BINGGU_HOME 복구는 호출부 finally 담당.

    회상 검증(2026-07 하드닝 · 4cli 반영): 새 프로세스(자식) 회상만 신뢰한다. same-process fallback 없음
    — 자식 프로세스가 실패하면 데모 전체를 실패(return 1)로 종료한다. 통과 기준은 '회상'이라는 문자열이
    아니라, 승인한 기억의 content 가 자식 회상 출력에 정확히 존재하고 + 비승인 후보는 존재하지 않는 것이다.
    """
    import hashlib
    import subprocess
    from openbinggu_deprecate_and_remind_g3 import open_g3
    import binggu_save_gate as sgate

    non_interactive = bool(getattr(a, "non_interactive", False))
    ledger = os.path.join(demo_home, "ledger.sqlite")
    snap_dir = os.path.join(demo_home, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)

    convo = DEMO_SCENARIO["input"]
    marker = DEMO_SCENARIO["approve_marker"]
    query = DEMO_SCENARIO["query"]

    print("=" * 60)
    print("BingguPack 데모 — AI가 기억해도, 결정권은 나에게")
    print("=" * 60)
    print("격리 데모 장부: %s" % ledger)
    print("(운영 장부·기억 데이터에는 쓰지 않습니다 · 운영 홈: %s)\n" % op_home)

    # 빈 격리 장부 생성 + 승인 전 활성 기억 수 확인.
    db = open_g3(ledger)
    active_before = db.con.execute("SELECT COUNT(*) FROM nodes WHERE state='active'").fetchone()[0]
    db.close()

    # 1) 후보 발견 (write 0)
    pv = binggu.capture_preview(convo)
    cands = pv["candidates"]
    print("[1] 대화에서 기억 후보를 발견했습니다 (아직 저장 안 함):")
    print("    입력: \"%s\"\n" % convo)
    for j, c in enumerate(cands, 1):
        print("    [%d] (%s) %s" % (j, c.get("label_kind"), c["sentence"]))
    print("\n    현재 활성 기억: %d개 — 승인 전에는 아무것도 확정되지 않습니다.\n" % active_before)

    # 시나리오 계약: 승인 후보 정확히 1개 + 비승인 후보 최소 1개(개수 하드코딩 대신 구조 보장).
    #   승인 대상은 approve_marker(내용)로 식별 → 분류기 순서 변동에 강건.
    approve_idx = [j for j, c in enumerate(cands, 1) if marker in c["sentence"]]
    reject_idx = [j for j, c in enumerate(cands, 1) if j not in approve_idx]
    if len(approve_idx) != 1 or len(reject_idx) < 1:
        print("데모 시나리오 계약 위반: 승인 후보 %d개·비승인 %d개 (기대: 승인 1 · 비승인 ≥1)"
              % (len(approve_idx), len(reject_idx)))
        return 1

    # 2) 검토·승인 — 승인 대상은 번호가 아니라 내용(approve_marker)으로 결정.
    if non_interactive:
        picks = list(approve_idx)
        print("[2] (비대화형) '%s' 이(가) 든 후보 [%d] 만 승인합니다 — 데모 격리 홈에서만 시뮬레이션.\n"
              % (marker, approve_idx[0]))
    else:
        default = ",".join(str(i) for i in approve_idx)
        try:
            raw = input("[2] 저장할 후보 번호를 고르세요 (쉼표로 여러 개 · 기본 %s): " % default).strip()
        except (EOFError, KeyboardInterrupt):
            raw = ""
            print()
        raw = raw or default
        try:
            picks = [int(x) for x in raw.split(",") if x.strip()]
        except ValueError:
            picks = list(approve_idx)
        picks = [i for i in picks if 1 <= i <= len(cands)] or list(approve_idx)
        print()

    rejected = [c["sentence"] for j, c in enumerate(cands, 1) if j not in picks]

    # 3) 승인 앵커(격리 홈) → 저장 — 실흐름 재현: preview 영속 + 사람 'SAVE n' 발화 기록(ref 바인딩).
    #    save_selected 의 core 재승격이 (preview_ref, idx) 를 소비해 승격·저장한다.
    #    운영 홈이 아니라 데모 홈의 last_preview/save_gate_log 에만 기록된다(운영 승인 우회 아님).
    confirm = "SAVE " + ",".join(str(i) for i in picks)
    sgate.write_last_preview(cands)
    sgate.gate_record_from_prompt(confirm)
    db = open_g3(ledger)
    r = binggu.save_selected(db, convo, picks, {"actor": "reader", "confirm": confirm}, snap_dir)
    active_after = db.con.execute("SELECT COUNT(*) FROM nodes WHERE state='active'").fetchone()[0]
    stored = [row[0] for row in db.con.execute("SELECT sentence FROM nodes WHERE state='active'")]
    db.close()

    if not r.get("applied"):
        print("데모 저장 실패: %s" % r.get("reason"))
        return 1

    print("[3] 승인한 항목만 로컬 장부에 확정 기록했습니다.")
    print("    ✓ 저장 %d개 — 활성 기억 %d → %d" % (r.get("saved"), active_before, active_after))
    for s in stored:
        print("      · %s" % s)
    for s in rejected:
        print("    ✗ 고르지 않은 후보는 저장되지 않음: %s" % s)
    print()

    # 승인·저장된 기억(정확히 1건) + content digest — 새 프로세스 회상 결과와 대조할 기준.
    approved_sentence = stored[0] if stored else cands[picks[0] - 1]["sentence"]
    approved_digest = hashlib.sha256(approved_sentence.encode("utf-8")).hexdigest()

    # 4) 새 프로세스에서 회상 — 자식 프로세스만 신뢰(same-process fallback 없음 · 조용한 우회 제거).
    print("[4] 새 프로세스에서 회상 — \"%s\"" % query)
    print("    (자식: python -m binggu recall · same-process fallback 없음)")
    child_ok, child_stdout, child_reason = False, "", ""
    try:
        env = dict(os.environ)
        env["BINGGU_HOME"] = demo_home
        out = subprocess.run(
            [sys.executable, "-m", "binggu", "--ledger", ledger, "recall", query],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, timeout=90)
        child_stdout = out.stdout or ""
        if out.returncode != 0:
            child_reason = "자식 프로세스 실패(returncode=%s)" % out.returncode
        else:
            child_ok = True
    except Exception as e:  # subprocess 실행 불가 → 데모 실패(same-process 로 우회하지 않음)
        child_reason = "자식 프로세스 예외: %s" % e

    # 구조화 판정: 자식 성공 + 승인 content 정확 포함 + 비승인 content 부재.
    recall_has_approved = approved_sentence in child_stdout
    recall_has_rejected = any(s in child_stdout for s in rejected)
    if not (child_ok and recall_has_approved and not recall_has_rejected):
        print("    ✗ 새 프로세스 회상 검증 실패 — 데모를 실패로 종료합니다.")
        if child_reason:
            print("      사유: %s" % child_reason)
        elif not recall_has_approved:
            print("      사유: 승인한 기억이 새 프로세스 회상 결과에 없음")
        else:
            print("      사유: 승인하지 않은 후보가 회상 결과에 나타남")
        return 1

    print("    ✓ 새 프로세스가 승인한 기억을 회상 — content digest 일치")
    print("      memory-digest(sha256) = %s" % approved_digest)
    for line in child_stdout.splitlines():
        ls = line.strip()
        if ls and not ls.startswith("→") and "mark-" not in ls:
            print("      " + line)
    print("    ✓ 승인하지 않은 후보 %d건은 회상 결과에 없음" % len(rejected))
    print()

    # 5) 근거/이력 확인 (provenance)
    import binggu_recall as RC
    node_id = (r.get("node_ids") or [None])[0]
    if node_id:
        print("[5] 이 기억이 무엇에 근거하는지 확인 (provenance):")
        tr = RC.judgment_trace(ledger, node_id, home=demo_home)
        if tr.get("found"):
            print("    기억: %s" % tr["root"]["claim"])
            print("    근거: 원문 발화에서 캡처(evidence_supports 연결) · memory-id = %s" % node_id)
        print("    더 보기:  binggu explain %s\n" % node_id)

    # 6) 정리 안내 (실제 삭제/BINGGU_HOME 복구는 cmd_demo finally 담당)
    print("-" * 60)
    if created_tmp and not keep:
        print("데모 데이터를 정리했습니다(임시 폴더 삭제).")
    else:
        print("데모 데이터 위치: %s  (직접 삭제 가능)" % demo_home)
    print("실제 장부 시작:  binggu init")
    print("=" * 60)
    return 0
