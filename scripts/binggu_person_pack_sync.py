# -*- coding: utf-8 -*-
"""사람축(owner) 온톨로지 팩 자동 동기화 — 세션 자동 갱신용.

owner 결정(2026-07-02): 사용자 온톨로지를 opencrab 팩으로 올리고 지속 자동 업데이트.
Agent 경유 업로드가 harness 통과(무인 스케줄러/시크릿파일은 auto mode 차단) →
매 세션 시작/handoff 때 이 스크립트로 변경 감지 후 변경 시에만 Claude 가 Agent 로
opencrab_pack_update 를 호출한다. 실제 클라우드 호출은 MCP(Agent) — hook 은 불가.

파이프:
  1. ledger 의 speaker=owner active 노드 → T3 필터(PII·과거사 하드제외) → pack text.
  2. 이전 스냅샷(person_pack_last.json)의 content_hash 와 비교.
  3. NO_CHANGE / UPDATE_NEEDED / BASELINE_SET 판정(JSON) 반환.
  4. Agent 가 업로드 성공하면 --confirm 으로 baseline 갱신(다음 변경 감지 기준).

read-only(ledger mode=ro). 상태파일만 write(운영 ledger 불변)."""
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sqlite3  # noqa: E402
from binggu_t3_filter import filter_uploadable  # noqa: E402

# owner 온톨로지 팩(2026-07-02 최초 업로드) — 갱신 대상 고정.
PACK_ID = "4da76877-e286-449f-8116-569be4056838"
STATE_FILE = "person_pack_last.json"
PACK_TITLE = "사장님 의사결정 원칙 온톨로지"


def _home():
    return os.environ.get("BINGGU_HOME") or os.path.join(os.path.expanduser("~"), ".binggupack")


def _ledger():
    return os.environ.get("BINGGU_LEDGER") or os.path.join(_home(), "ledger.sqlite")


def _state_path():
    return os.path.join(_home(), STATE_FILE)


def build_pack_text(ledger=None):
    """owner active 노드 → T3 통과분 → pack text. 반환 (text, ok_count, blocked_count)."""
    L = ledger or _ledger()
    con = sqlite3.connect("file:%s?mode=ro" % str(L).replace("\\", "/"), uri=True)
    try:
        rows = con.execute(
            "SELECT sentence FROM nodes WHERE speaker='owner' AND state='active'").fetchall()
    finally:
        con.close()
    items = [{"sentence": r[0]} for r in rows if r[0]]
    fr = filter_uploadable(items)
    seen, uniq = set(), []
    for it in fr["ok"]:
        s = (it["sentence"] or "").strip()
        if s and s not in seen:
            seen.add(s)
            uniq.append(s)
    lines = ["# 사장님(owner) 의사결정 원칙·판단 온톨로지", "",
             "사용자 개인 온톨로지 — owner 화자 확정 원칙/판단 (T3 하드제외 통과분).", ""]
    for s in uniq:
        lines.append("- " + s)
    return "\n".join(lines), len(uniq), len(fr["blocked"])


def load_state():
    p = _state_path()
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(content_hash, count):
    st = {"pack_id": PACK_ID, "content_hash": content_hash, "count": count}
    with open(_state_path(), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    return st


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def sync(baseline=False, write_text=None, ledger=None):
    """변경 감지. baseline=True → 현재 상태를 기준으로 기록(업로드 안 함).
    반환 {status, pack_id, count, blocked, hash, ...}."""
    text, count, blocked = build_pack_text(ledger=ledger)
    h = _hash(text)
    if write_text:
        with open(write_text, "w", encoding="utf-8") as f:
            f.write(text)
    st = load_state()
    if baseline:
        save_state(h, count)
        return {"status": "BASELINE_SET", "pack_id": PACK_ID, "count": count,
                "blocked": blocked, "hash": h}
    if st.get("content_hash") == h:
        return {"status": "NO_CHANGE", "pack_id": PACK_ID, "count": count, "hash": h}
    return {"status": "UPDATE_NEEDED", "pack_id": PACK_ID, "count": count, "blocked": blocked,
            "hash": h, "prev_hash": st.get("content_hash"), "prev_count": st.get("count")}


def confirm(content_hash, count):
    """Agent 가 opencrab 업로드 성공 후 호출 — baseline 갱신(다음 변경 감지 기준)."""
    return save_state(content_hash, count)


# ---------------- selftest ----------------
def _selftest():
    import tempfile
    import shutil
    ok = True

    def check(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
        print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")

    tmp = tempfile.mkdtemp(prefix="person_sync_")
    try:
        home = os.path.join(tmp, ".binggupack")
        os.makedirs(home)
        ledger = os.path.join(home, "ledger.sqlite")
        os.environ["BINGGU_HOME"] = home
        os.environ["BINGGU_LEDGER"] = ledger
        con = sqlite3.connect(ledger)
        con.execute("CREATE TABLE nodes(node_id TEXT PRIMARY KEY, node_type TEXT, sentence TEXT,"
                    " state TEXT, semantic_subtype TEXT, speaker TEXT)")

        def add(nid, sent, speaker="owner", state="active"):
            con.execute("INSERT INTO nodes(node_id,node_type,sentence,state,semantic_subtype,speaker)"
                        " VALUES(?,?,?,?,?,?)", (nid, "judgment", sent, state, "선호", speaker))
            con.commit()

        add("o1", "결론부터 짧게 답한다")
        add("o2", "유연함이 능력이다")
        add("p1", "이건 PII 010-1234-5678 포함", speaker="owner")  # T3 차단 대상
        add("a1", "AI 발화는 온톨로지 팩에서 제외", speaker="ai")   # 사람축 아님

        # T1 build: owner 만·T3 차단 제외
        text, cnt, blk = build_pack_text()
        check(cnt == 2 and blk == 1 and "결론부터" in text and "010-1234" not in text,
              "T1 build: owner 2노드·T3 차단 1·PII 미포함")
        # T2 첫 sync = UPDATE_NEEDED(상태 없음)
        r1 = sync()
        check(r1["status"] == "UPDATE_NEEDED" and r1["count"] == 2, "T2 상태 없음 → UPDATE_NEEDED")
        # T3 baseline 기록 → NO_CHANGE
        rb = sync(baseline=True)
        check(rb["status"] == "BASELINE_SET", "T3 baseline 기록")
        r2 = sync()
        check(r2["status"] == "NO_CHANGE", "T4 baseline 후 변경 없음 → NO_CHANGE")
        # T5 owner 노드 추가 → UPDATE_NEEDED
        add("o3", "새 판단 하나 추가됨")
        r3 = sync()
        check(r3["status"] == "UPDATE_NEEDED" and r3["count"] == 3, "T5 노드 추가 → UPDATE_NEEDED(count 3)")
        # T6 confirm 후 다시 NO_CHANGE
        confirm(r3["hash"], r3["count"])
        r4 = sync()
        check(r4["status"] == "NO_CHANGE", "T6 confirm(업로드 성공 기록) 후 → NO_CHANGE")
        # T7 write_text 파일 생성
        wt = os.path.join(tmp, "pack.txt")
        sync(write_text=wt)
        check(os.path.exists(wt) and "새 판단" in open(wt, encoding="utf-8").read(),
              "T7 write_text 팩 파일 생성")
        # T8 ledger read-only(mtime 불변)
        m0 = os.path.getmtime(ledger)
        sync()
        check(os.path.getmtime(ledger) == m0, "T8 sync 후 ledger mtime 불변(read-only)")

        con.close()
        print(f"\nGATE={'GO' if ok else 'NO-GO'}")
        return 0 if ok else 1
    finally:
        os.environ.pop("BINGGU_HOME", None)
        os.environ.pop("BINGGU_LEDGER", None)
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="binggu_person_pack_sync")
    ap.add_argument("--baseline", action="store_true", help="현재 상태를 기준으로 기록(업로드 안 함)")
    ap.add_argument("--write-text", default=None, help="pack text 를 파일로 출력(Agent 업로드용)")
    ap.add_argument("--confirm-hash", default=None, help="Agent 업로드 성공 후 baseline 갱신용 hash")
    ap.add_argument("--confirm-count", type=int, default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.confirm_hash is not None and a.confirm_count is not None:
        print(json.dumps(confirm(a.confirm_hash, a.confirm_count), ensure_ascii=False))
        return 0
    r = sync(baseline=a.baseline, write_text=a.write_text)
    print(json.dumps(r, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
