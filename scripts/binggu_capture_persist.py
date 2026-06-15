"""빙구팩 영속 candidate 캡처 버퍼 — 기본 OFF · scope 게이트 · TTL · rollback.

설계: 영속 candidate buffer 선행 과제 (hook 실등록은 본 모듈 GO 이후 별도 단계).
원칙(전부 본 모듈에서 강제·셀프테스트로 증명):
  - candidate-only: state 항상 'captured_candidate' (active/confirmed/ledger write 0)
  - 원문(대화 전문) 미저장: 발화 TEXT_CAP(문장 전체 보존 상한) 초과분 truncate
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

TEXT_CAP = 1000         # 자동수집 버퍼 발화 보존 상한 = capture_preview.MAX_NODE_SENTENCE 정합(owner GO 2026-06-15).
                        # 문장 전체 보존(80자 발췌 폐기 — 개인 온톨로지 정체성). 1000 초과 = 대화 덩어리 → 절단(원문=대화 전문 저장 금지).
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
        self.paused_flag = self.home / "capture_paused"
        self.scope_file = self.home / "capture_scope.json"
        self.sem_preview_flag = self.home / "semantic_preview_enabled"  # opt-in 기본 OFF
        self.rationale_preview_flag = self.home / "rationale_preview_enabled"  # 2층 opt-in 기본 OFF

    def enabled(self):
        """기본 OFF: capture_enabled 플래그 존재 AND capture_paused 없음."""
        return self.flag.exists() and not self.paused_flag.exists()

    def paused(self):
        return self.paused_flag.exists()

    def _scope(self):
        empty = {"global": False, "allowed_cwd_prefixes": [], "denied_cwd_substrings": []}
        if not self.scope_file.exists():
            return empty
        try:
            d = json.loads(self.scope_file.read_text(encoding="utf-8"))
            return {
                "global": bool(d.get("global", False)),
                "allowed_cwd_prefixes": list(d.get("allowed_cwd_prefixes", [])),
                "denied_cwd_substrings": list(d.get("denied_cwd_substrings", [])),
            }
        except Exception:
            return empty

    def in_scope(self, cwd):
        """deny 우선 → global(전역) → allow 화이트리스트. allow 비면 fail-closed(False).
        global=true 라도 denied_cwd_substrings 는 항상 우선 차단."""
        sc = self._scope()
        cwd_n = _norm(cwd)
        for d in sc["denied_cwd_substrings"]:
            if _norm(d) in cwd_n:
                return False
        if sc["global"]:
            return True
        allow = sc["allowed_cwd_prefixes"]
        if not allow:
            return False
        return any(cwd_n.startswith(_norm(a)) for a in allow)

    def should_capture(self, cwd):
        return self.enabled() and self.in_scope(cwd)

    def semantic_preview(self):
        """opt-in 기본 OFF: semantic_preview_enabled 플래그 있을 때만 shadow subtype 보조 라벨 표시.
        capture 결정과 무관 — 표시 전용 게이트."""
        return self.sem_preview_flag.exists()

    def rationale_preview(self):
        """opt-in 기본 OFF: rationale_preview_enabled 플래그 있을 때만 2층 근거/엣지 추천 표시.
        추천만 — capture 결정·저장과 무관(read-only)."""
        return self.rationale_preview_flag.exists()


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

    def render_preview(self, now=None, semantic=None):
        """captured 후보 목록. semantic 인자는 테스트 주입용(미지정 시 opt-in ON에서 lazy 생성)."""
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
        if self.scope.semantic_preview():
            self._attach_semantic(items, semantic)
        result = {"count": len(items), "items": items, "note": "owner 승인 전 candidate (active 아님)"}
        if self.scope.rationale_preview():
            self._attach_rationale(items, result)
        return result

    def _attach_rationale(self, items, result):
        """opt-in ON 시 2층 근거/엣지 추천을 read-only 부착. 추천만 — 저장·DB·ledger 미접촉.
        capture 단계 후보엔 evidence 미첨부라 edge는 보통 보류(rationale만). 실패 시 무변화(graceful)."""
        if not items:
            return
        try:
            from binggu_rationale_suggest import suggest_rationale
            from openbinggu_label_kind_map import classify_label_kind
            cands = [{
                "text": it["text"],
                "label_kind": classify_label_kind(it["text"])[0],   # canonical(정규식·정본)
                "semantic_subtype": (it.get("semantic") or {}).get("subtype"),  # 1층 보조(있으면)
                "confidence": it.get("confidence"),
                "evidence_refs": [],   # capture 후보엔 원문증빙 없음(pack 빌드 단계에서 첨부)
            } for it in items]
            rec = suggest_rationale(cands)
            for it, ra in zip(items, rec["rationale"]):
                it["rationale"] = {"why": ra["rationale"], "caveat": ra["caveat"]}
            result["suggested_edges"] = rec["suggested_edges"]   # 보통 [](evidence 보류)
            result["rationale_note"] = rec["note"]
        except Exception:
            return

    def _attach_semantic(self, items, semantic=None):
        """opt-in ON 시 captured 후보에만 shadow subtype 보조 라벨 부착 (read-only).
        방어선: cos = subtype 추천/설명 전용 — capture 결정·state·DB·ledger 일체 미접촉.
        shadow/Ollama 실패 시 무변화(graceful) — 기존 출력 보존."""
        if not items:
            return
        try:
            if semantic is None:
                from binggu_semantic_shadow import get_cached_shadow
                semantic = get_cached_shadow()  # 프로세스 내 캐시(centroid 재계산 1회)
            for it in items:
                sug = semantic.subtype_suggestion(it["text"])
                if sug:
                    it["semantic"] = {  # canonical 아님 — shadow 보조 필드
                        "subtype": sug["sem_subtype"], "score": sug["sem_conf"], "band": sug["band"],
                        "note": f"추천 subtype(보조·cos): {sug['sem_subtype']} ({sug['band']}, {sug['sem_conf']})",
                    }
        except Exception:
            return

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

        # T7 원문(대화 덩어리) 전문 미저장: TEXT_CAP(=문장 전체 상한) 초과만 truncate.
        #     짧은 발화는 온전히 보존(문장 전체 정체성) — 1000 초과 덩어리만 절단.
        midtext = "B안으로 결정한다 " + ("가" * 300)  # ~308자 < TEXT_CAP → 전체 보존(절단 0)
        r_mid = buf.feed(midtext, repo_cwd)
        pv_mid = buf.render_preview()
        kept = next(it["text"] for it in pv_mid["items"] if it["text"].startswith("B안으로 결정한다 가"))
        check(not r_mid.get("truncated") and kept == midtext.strip(),
              f"T7 문장 전체 보존(< {TEXT_CAP}자 절단 0)")
        longtext = "B안으로 결정한다 " + ("가" * 1100)  # > TEXT_CAP → 대화 덩어리로 간주 절단
        r = buf.feed(longtext, repo_cwd)
        pv = buf.render_preview()
        stored_text = max((it["text"] for it in pv["items"]
                           if it["text"].startswith("B안으로 결정한다 가")), key=len)
        check(r.get("truncated") and len(stored_text) == TEXT_CAP,
              f"T7b 대화 덩어리(> {TEXT_CAP}자) 절단(원문 전문 저장 금지)")

        # T8 candidate-only: state 항상 captured_candidate
        check(all(it["state"] == "captured_candidate" for it in pv["items"]),
              "T8 candidate-only(active/confirmed 0)")

        # T9 영속 round-trip: 새 인스턴스 재오픈 시 누적 유지 (hook 발화간 누적 핵심)
        size_before = buf.size
        buf2 = PersistentCaptureBuffer(home=home)
        check(buf2.size == size_before and size_before == 3,
              "T9 영속 round-trip(새 인스턴스 재오픈 누적 유지, size=3)")

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

        # T14 pause: capture_paused 플래그 → enabled여도 should_capture False
        (home / "capture_paused").write_text("1", encoding="utf-8")
        check(scope.paused() and not scope.should_capture(repo_cwd),
              "T14 pause 플래그 → should_capture False")
        (home / "capture_paused").unlink()
        check(scope.should_capture(repo_cwd), "T14b resume → should_capture True")

        # T15 global scope: 타 cwd 허용 · deny 는 여전히 우선 차단
        scope.scope_file.write_text(json.dumps({
            "global": True, "allowed_cwd_prefixes": [], "denied_cwd_substrings": ["bid-engine"],
        }, ensure_ascii=False), encoding="utf-8")
        check(scope.in_scope("D:/anywhere/else") and not scope.in_scope(other_cwd),
              "T15 global scope → 타 cwd 허용 · deny(bid-engine) 차단 유지")

        # T16~T19 semantic shadow preview opt-in 실배선 (read-only 보조 라벨)
        import hashlib
        import math
        from binggu_semantic_shadow import SemanticShadow

        def mock_embed(text, timeout=10):  # 결정적 mock — Ollama 미접촉
            h = hashlib.sha256(text.encode("utf-8")).digest()
            v = [h[i % len(h)] / 255.0 for i in range(64)]
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            return [x / n for x in v]

        buf3 = PersistentCaptureBuffer(home=home)
        buf3.feed("B안으로 결정한다", repo_cwd)  # captured 후보 1건 확보
        scope2 = CaptureScope(home=home)
        sem = SemanticShadow(embed_fn=mock_embed)

        # T16 opt-in 기본 OFF → semantic 필드 없음(완전 무변화)
        check(not scope2.semantic_preview(), "T16 semantic preview 기본 OFF")
        pv_off = buf3.render_preview(semantic=sem)
        check(pv_off["count"] >= 1 and all("semantic" not in it for it in pv_off["items"]),
              "T16b OFF → 보조 필드 없음(기존 출력 무변화)")

        # T17 opt-in ON → captured 후보에만 subtype 보조 라벨
        scope2.sem_preview_flag.write_text("1", encoding="utf-8")
        ledger_mtime_b = ledger.stat().st_mtime_ns
        db_mtime_b = buf3.db_path.stat().st_mtime_ns
        pv_on = buf3.render_preview(semantic=sem)
        valid_sub = {"교훈", "결정", "선호", "설계결정", "버그패턴", "사실"}
        check(pv_on["count"] >= 1 and all("semantic" in it and it["semantic"]["subtype"] in valid_sub
              for it in pv_on["items"]),
              "T17 ON → captured 후보에 subtype 보조 라벨")
        check(all(it["state"] == "captured_candidate" for it in pv_on["items"]),
              "T17b 보조 라벨이 capture state 미변경(여전히 candidate)")

        # T18 semantic preview = read-only: ledger·buffer DB write 0
        check(ledger.read_bytes() == b"LEDGER-SENTINEL" and ledger.stat().st_mtime_ns == ledger_mtime_b,
              "T18 ON preview → 운영 ledger 미접촉(write 0)")
        check(buf3.db_path.stat().st_mtime_ns == db_mtime_b,
              "T18b ON preview → buffer DB 미변경(read-only)")

        # T19 ignored 발화는 buffer 미저장 → preview(ON)에도 미표시
        before = buf3.render_preview(semantic=sem)["count"]
        buf3.feed("ㅋㅋ 웃기네", repo_cwd)  # ignored
        pv2 = buf3.render_preview(semantic=sem)
        check(pv2["count"] == before and all("웃기네" not in it["text"] for it in pv2["items"]),
              "T19 ignored 발화 → preview ON 에도 미표시(should_capture 결정 불변)")
        scope2.sem_preview_flag.unlink()

        # T20 캐시 hit: preview ON 2회 → SemanticShadow 1회만 생성(centroid 재계산 병목 제거)
        import binggu_semantic_shadow as bss
        bss._SHADOW_CACHE.clear()
        build_count = {"n": 0}
        _orig_shadow = bss.SemanticShadow

        class _CountShadow(_orig_shadow):
            def __init__(self, *a, **k):
                build_count["n"] += 1
                k.setdefault("embed_fn", mock_embed)  # Ollama 미접촉
                super().__init__(*a, **k)

        bss.SemanticShadow = _CountShadow
        db_mtime_c = buf3.db_path.stat().st_mtime_ns
        ledger_mtime_c = ledger.stat().st_mtime_ns
        try:
            scope2.sem_preview_flag.write_text("1", encoding="utf-8")
            pv_c1 = buf3.render_preview()   # semantic=None → get_cached_shadow → 생성 1
            pv_c2 = buf3.render_preview()   # 캐시 hit → 생성 0
            scope2.sem_preview_flag.unlink()
        finally:
            bss.SemanticShadow = _orig_shadow
            bss._SHADOW_CACHE.clear()
        check(build_count["n"] == 1, "T20 캐시 hit: preview ON 2회 → SemanticShadow 1회만 생성")
        check(all("semantic" in it for it in pv_c2["items"]) and len(pv_c2["items"]) >= 1,
              "T20b 캐시 경로도 captured 후보에 subtype 보조 라벨 정상")
        check(buf3.db_path.stat().st_mtime_ns == db_mtime_c and ledger.stat().st_mtime_ns == ledger_mtime_c,
              "T20c 캐시 preview → buffer DB·ledger write 0(read-only)")

        # T21~T23 2층 rationale preview opt-in (read-only)
        # T21 기본 OFF → rationale 필드 없음(무변화)
        check(not scope2.rationale_preview(), "T21 rationale preview 기본 OFF")
        pv_r_off = buf3.render_preview(semantic=sem)
        check(pv_r_off.get("count", 0) >= 1 and all("rationale" not in it for it in pv_r_off["items"])
              and "suggested_edges" not in pv_r_off,
              "T21b OFF → rationale/suggested_edges 없음(무변화)")

        # T22 ON → captured 후보에만 rationale, evidence 미첨부라 edge 보류
        scope2.rationale_preview_flag.write_text("1", encoding="utf-8")
        db_mtime_r = buf3.db_path.stat().st_mtime_ns
        ledger_mtime_r = ledger.stat().st_mtime_ns
        pv_r_on = buf3.render_preview(semantic=sem)
        check(all("rationale" in it and "why" in it["rationale"] and "candidate" in it["rationale"]["caveat"]
                  for it in pv_r_on["items"]),
              "T22 ON → captured 후보에 rationale(why+candidate caveat)")
        check(pv_r_on.get("suggested_edges") == [],
              "T22b capture 단계 evidence 미첨부 → edge 보류(rationale만)")
        check(all(it["state"] == "captured_candidate" for it in pv_r_on["items"]),
              "T22c rationale이 capture state 미변경")

        # T23 read-only: buffer DB·ledger write 0
        check(buf3.db_path.stat().st_mtime_ns == db_mtime_r and ledger.stat().st_mtime_ns == ledger_mtime_r,
              "T23 rationale preview ON → buffer DB·ledger write 0")
        scope2.rationale_preview_flag.unlink()

        gate = "GO" if ok else "NO-GO"
        print(f"\nGATE={gate}")
        return ok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
