"""빙구팩 영속 candidate 캡처 버퍼 — 기본 OFF · scope 게이트 · TTL · rollback.

설계: 영속 candidate buffer 선행 과제 (hook 실등록은 본 모듈 GO 이후 별도 단계).
원칙(전부 본 모듈에서 강제·셀프테스트로 증명):
  - candidate-only: state 항상 'captured_candidate' (active/confirmed/ledger write 0)
  - 원문 전문 미저장: 발화 TEXT_CAP(발췌 상한) 초과분 truncate
  - 기본 OFF: ~/.binggupack/capture_enabled 플래그 있을 때만 동작
  - scope 게이트: repo/session 화이트리스트(fail-closed) + deny 우선 → bid-engine 등 타 세션 제외
  - TTL 자동 폐기: captured_at + ttl_days 경과분 lazy purge
  - rollback: capture_buffer.sqlite 단일 파일 삭제 1회로 완전 원복 (운영 ledger.sqlite 미접촉)

hook은 등록하지 않음(미래 별도 GO). 본 모듈은 should_capture 게이트 + 영속 store만 제공.
"""
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

from binggu_capture_classifier import classify

TEXT_CAP = 80           # 원문 전문 저장 금지: candidate 발췌 상한(정본 capture_preview/save_selected ≤80자 기준)
DEFAULT_TTL_DAYS = 7    # TTL 자동 폐기 기본값


def binggu_home(home=None):
    """buffer 루트. 테스트는 home 인자/ BINGGU_HOME 으로 운영 경로 미접촉."""
    if home:
        return Path(home)
    env = os.environ.get("BINGGU_HOME")
    if env:
        return Path(env)
    return Path.home() / ".binggupack"


def _norm(p):
    return str(Path(p)).replace("\\", "/").lower()


class CaptureScope:
    """capture 2중 게이트: 기본 OFF 플래그 AND repo/session scope 화이트리스트."""

    def __init__(self, home=None):
        self.home = binggu_home(home)
        self.flag = self.home / "capture_enabled"
        self.scope_file = self.home / "capture_scope.json"

    def enabled(self):
        """기본 OFF: 플래그 파일 존재해야만 True."""
        return self.flag.exists()

    def _scope(self):
        if not self.scope_file.exists():
            return {"allowed_cwd_prefixes": [], "denied_cwd_substrings": []}
        try:
            d = json.loads(self.scope_file.read_text(encoding="utf-8"))
            return {
                "allowed_cwd_prefixes": list(d.get("allowed_cwd_prefixes", [])),
                "denied_cwd_substrings": list(d.get("denied_cwd_substrings", [])),
            }
        except Exception:
            return {"allowed_cwd_prefixes": [], "denied_cwd_substrings": []}

    def in_scope(self, cwd):
        """deny 우선 → allow 화이트리스트. allow 비면 fail-closed(False)."""
        sc = self._scope()
        cwd_n = _norm(cwd)
        for d in sc["denied_cwd_substrings"]:
            if _norm(d) in cwd_n:
                return False
        allow = sc["allowed_cwd_prefixes"]
        if not allow:
            return False
        return any(cwd_n.startswith(_norm(a)) for a in allow)

    def should_capture(self, cwd):
        return self.enabled() and self.in_scope(cwd)


class PersistentCaptureBuffer:
    """발화 간 누적되는 영속 candidate 버퍼. 게이트 통과 발화만 적재."""

    def __init__(self, home=None, ttl_days=DEFAULT_TTL_DAYS):
        self.home = binggu_home(home)
        self.scope = CaptureScope(home)
        self.ttl_days = ttl_days
        self.db_path = self.home / "capture_buffer.sqlite"
        self.ledger_path = self.home / "ledger.sqlite"  # 미접촉 대상(경로 참조만)

    # ---- store ----
    def _conn(self):
        self.home.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(self.db_path))
        c.execute(
            """CREATE TABLE IF NOT EXISTS capture_candidates(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                pinned INTEGER NOT NULL DEFAULT 0,
                confidence TEXT,
                signals TEXT,
                state TEXT NOT NULL DEFAULT 'captured_candidate',
                captured_at REAL NOT NULL,
                cwd TEXT)"""
        )
        return c

    def feed(self, utterance, cwd, prev_turn=None, now=None):
        """발화 1건. 게이트 통과 + captured_candidate 만 영속.
        게이트 차단 시 classify 조차 호출 안 함(타 세션 발화 미분류)."""
        now = time.time() if now is None else now
        if not self.scope.should_capture(cwd):
            return {"action": "skipped_scope", "stored": False,
                    "enabled": self.scope.enabled(), "in_scope": self.scope.in_scope(cwd)}
        v = classify(utterance, prev_turn)
        if v["state"] == "preview_trigger":
            self._purge(now)
            return {"action": "preview", "verdict": v, "preview": self.render_preview(now)}
        if v["state"] != "captured_candidate":
            return {"action": "ignored", "verdict": v, "stored": False}
        text = utterance.strip()
        truncated = len(text) > TEXT_CAP
        if truncated:
            text = text[:TEXT_CAP]  # 원문 전문 저장 금지
        c = self._conn()
        try:
            c.execute(
                "INSERT INTO capture_candidates(text,pinned,confidence,signals,state,captured_at,cwd)"
                " VALUES(?,?,?,?,?,?,?)",
                (text, 1 if v["pinned"] else 0, v["confidence"],
                 json.dumps(list(v["signals"]), ensure_ascii=False),
                 "captured_candidate", now, str(cwd)),
            )
            c.commit()
            self._purge(now, conn=c)
        finally:
            c.close()
        return {"action": "captured", "verdict": v, "stored": True, "truncated": truncated}

    def _purge(self, now, conn=None):
        """TTL 경과분 삭제. 반환=삭제 건수."""
        cutoff = now - self.ttl_days * 86400
        own = conn is None
        c = conn or self._conn()
        try:
            n = c.execute("DELETE FROM capture_candidates WHERE captured_at < ?", (cutoff,)).rowcount
            c.commit()
            return n
        finally:
            if own:
                c.close()

    def render_preview(self, now=None):
        now = time.time() if now is None else now
        c = self._conn()
        try:
            self._purge(now, conn=c)
            rows = c.execute(
                "SELECT text,pinned,confidence FROM capture_candidates ORDER BY pinned DESC, id ASC"
            ).fetchall()
        finally:
            c.close()
        items = []
        for i, (text, pinned, conf) in enumerate(rows, 1):
            tags = []
            if pinned:
                tags.append("PINNED")
            if conf == "weak":
                tags.append("약함")
            tag = f" [{' · '.join(tags)}]" if tags else ""
            items.append({
                "idx": i, "text": text, "pinned": bool(pinned), "confidence": conf,
                "label": f"{i}. {text}{tag}", "state": "captured_candidate",
            })
        return {"count": len(items), "items": items, "note": "owner 승인 전 candidate (active 아님)"}

    @property
    def size(self):
        if not self.db_path.exists():
            return 0
        c = self._conn()
        try:
            return c.execute("SELECT COUNT(*) FROM capture_candidates").fetchone()[0]
        finally:
            c.close()

    # ---- rollback ----
    def backup(self):
        if not self.db_path.exists():
            return None
        bak = self.db_path.with_suffix(".sqlite.bak")
        shutil.copy2(self.db_path, bak)
        return bak

    def rollback(self):
        """buffer 완전 삭제 = rollback. 운영 ledger.sqlite 미접촉."""
        existed = self.db_path.exists()
        if existed:
            self.db_path.unlink()
        return existed


# ---------------- 셀프테스트 (temp home 전용, 운영 ~/.binggupack 미접촉) ----------------
def _selftest():
    import tempfile

    ok = True

    def check(cond, msg):
        nonlocal ok
        mark = "PASS" if cond else "FAIL"
        if not cond:
            ok = False
        print(f"  [{mark}] {msg}")
        return cond

    tmp = Path(tempfile.mkdtemp(prefix="bgp_capture_persist_"))
    try:
        home = tmp / ".binggupack"
        home.mkdir(parents=True)
        # 운영 ledger 미접촉 검증용 더미
        ledger = home / "ledger.sqlite"
        ledger.write_bytes(b"LEDGER-SENTINEL")
        ledger_mtime0 = ledger.stat().st_mtime_ns

        repo_cwd = "C:/Users/PC/binggupack"
        other_cwd = "C:/Users/PC/safety-app/bid-engine"

        scope = CaptureScope(home=home)

        # T1 기본 OFF: 플래그 없음 → 저장 0
        buf = PersistentCaptureBuffer(home=home)
        r = buf.feed("B안으로 결정", repo_cwd)
        check(r["action"] == "skipped_scope" and not r["stored"] and buf.size == 0,
              "T1 기본 OFF(플래그 없음) → skipped, 저장 0")

        # 플래그 ON
        scope.flag.write_text("1", encoding="utf-8")

        # T2 scope 미설정(allow 비어있음) → fail-closed
        r = buf.feed("B안으로 결정", repo_cwd)
        check(r["action"] == "skipped_scope" and buf.size == 0,
              "T2 플래그 ON + scope 화이트리스트 비어있음 → fail-closed 저장 0")

        # scope 설정: binggu 허용, bid-engine/safety-app deny
        scope.scope_file.write_text(json.dumps({
            "allowed_cwd_prefixes": ["C:/Users/PC/binggupack"],
            "denied_cwd_substrings": ["bid-engine", "safety-app"],
        }, ensure_ascii=False), encoding="utf-8")

        # T3 플래그 ON + scope 일치 → captured 영속
        r = buf.feed("이거 저장해", repo_cwd)
        check(r["action"] == "captured" and r["stored"] and buf.size == 1,
              "T3 플래그 ON + scope 일치 → captured 영속(size=1)")

        # T4 타 repo(bid-engine) → 게이트 차단 (allow 미매치 + deny 매치)
        r = buf.feed("B안으로 결정", other_cwd)
        check(r["action"] == "skipped_scope" and buf.size == 1,
              "T4 bid-engine 세션 발화 → 제외(size 불변=1)")

        # T5 deny 우선: 허용 prefix 안이라도 deny substring이면 차단
        r = buf.feed("결정했다", "C:/Users/PC/binggupack/bid-engine-notes")
        check(r["action"] == "skipped_scope" and buf.size == 1,
              "T5 deny substring 우선 → 허용 prefix 내부라도 차단")

        # T6 ignored 발화 → 저장 0
        r = buf.feed("ㅋㅋ 웃기네", repo_cwd)
        check(r["action"] == "ignored" and not r["stored"] and buf.size == 1,
              "T6 ignored 발화 → 저장 0(size 불변)")

        # T7 원문 전문 미저장: TEXT_CAP 초과 truncate
        longtext = "B안으로 결정한다 " + ("가" * 400)
        r = buf.feed(longtext, repo_cwd)
        pv = buf.render_preview()
        stored_text = next(it["text"] for it in pv["items"] if it["text"].startswith("B안으로 결정한다"))
        check(r.get("truncated") and len(stored_text) == TEXT_CAP,
              f"T7 원문 전문 미저장 → 발췌 cap({TEXT_CAP})로 truncate")

        # T8 candidate-only: state 항상 captured_candidate
        check(all(it["state"] == "captured_candidate" for it in pv["items"]),
              "T8 candidate-only(active/confirmed 0)")

        # T9 영속 round-trip: 새 인스턴스 재오픈 시 누적 유지 (hook 발화간 누적 핵심)
        size_before = buf.size
        buf2 = PersistentCaptureBuffer(home=home)
        check(buf2.size == size_before and size_before == 2,
              "T9 영속 round-trip(새 인스턴스 재오픈 누적 유지, size=2)")

        # T10 TTL 자동 폐기: 미래 시각으로 preview → 경과분 삭제
        future = time.time() + (DEFAULT_TTL_DAYS + 1) * 86400
        pv_future = buf2.render_preview(now=future)
        check(pv_future["count"] == 0 and buf2.size == 0,
              "T10 TTL 경과 → 자동 purge(size=0)")

        # T11 pinned 상단 정렬 (재적재 후 확인)
        buf2.feed("B안으로 결정한다", repo_cwd)  # normal
        buf2.feed("이거 저장해", repo_cwd)  # pinned
        pv2 = buf2.render_preview()
        check(pv2["count"] == 2 and pv2["items"][0]["pinned"],
              "T11 preview pinned 상단 정렬")

        # T12 rollback: 파일 삭제 → size 0
        bak = buf2.backup()
        existed = buf2.rollback()
        check(existed and buf2.size == 0 and not buf2.db_path.exists(),
              "T12 rollback(buffer 파일 삭제 → size 0)")
        check(bak is not None and bak.exists(), "T12b 백업 파일 생성됨")

        # T13 운영 ledger.sqlite 미접촉
        check(ledger.read_bytes() == b"LEDGER-SENTINEL" and ledger.stat().st_mtime_ns == ledger_mtime0,
              "T13 운영 ledger.sqlite 미접촉(내용·mtime 불변)")

        gate = "GO" if ok else "NO-GO"
        print(f"\nGATE={gate}")
        return ok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
