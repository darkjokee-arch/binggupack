# -*- coding: utf-8 -*-
"""Studio Approval Center 회귀 — effective-state 해석·exact-ID·nonce/path 미노출·review 무결성·
receipt sanitize·read-only 불변. 임시 ledger 격리 · 운영 ~/.binggupack 미접촉. 시각은 now 주입(결정적)."""
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from binggupack.studio import server, approval_view as av   # noqa: E402
from binggupack.safety import trusted_approval as ta        # noqa: E402
from openbinggu_owner_accept_ux import open_accept          # noqa: E402

NOW = 2_000_000_000
LID = "testledgerid01"
_SIDECAR = ("-wal", "-shm", "-journal")
_STATIC = os.path.join(ROOT, "binggupack", "studio", "static")


def _seed(ledger, home):
    db = open_accept(ledger)
    con = db.con
    P = ta.PROTOCOL_VERSION

    def req(rid, op, digest, summary, now=NOW):
        ta.upsert_request(con, rid, P, op, digest, LID, summary, now, 900, 32)

    req("req_pending01", "hosted_bundle", "digestP", "hosted_bundle: 1 intent(s)")
    req("req_approved1", "save_candidate", "digestA", "save_candidate: 1 item")
    ta.write_review(home, "req_approved1", "save_candidate",
                    {"text": "이 문장을 저장한다", "indices": [1], "explicit": True}, "digestA")
    ta.mint_approval(home, ta.get_request(con, "req_approved1"), 900, NOW)
    req("req_consumed1", "hosted_bundle", "digestC", "hosted_bundle: 2 intent(s)")
    appr = ta.mint_approval(home, ta.get_request(con, "req_consumed1"), 900, NOW)
    ta.reserve(con, appr["approval_nonce"], NOW)
    receipt = {"request_id": "req_consumed1", "operation": "hosted_bundle",
               "node_ids": ["node:CONV:aaaa1111", "node:CONV:missing99"],
               "decision_id": "dec_abc", "approval_nonce": "NONCE_MUST_NOT_LEAK", "actor": "human"}
    ta.finalize_consumed(con, appr["approval_nonce"], "req_consumed1", receipt, NOW)
    con.execute("INSERT INTO nodes(node_id,node_type,sentence,state,candidate,promotion_allowed) "
                "VALUES('node:CONV:aaaa1111','judgment','승인으로 생성된 기억','active',1,0)")
    req("req_rejected1", "deprecate", "digestR", "deprecate")
    ta.tombstone(home, ta.get_request(con, "req_rejected1"), "reject", NOW)
    req("req_expired01", "pair", "digestE", "pair", now=NOW - 1000)
    db.con.commit()
    db.close()


def _id8(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def _snap_files(home):
    snap = {}
    for root, _, files in os.walk(home):
        for f in files:
            if f.endswith(_SIDECAR):
                continue
            p = os.path.join(root, f)
            try:
                with open(p, "rb") as fh:
                    snap[os.path.relpath(p, home)] = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                pass
    return snap


class _Studio:
    def __init__(self, ledger):
        self.httpd, self.session = server.build_server(ledger, port=0)
        self.base = "http://127.0.0.1:%d/s/%s/" % (self.httpd.server_address[1], self.session)
        self._t = threading.Thread(target=self.httpd.serve_forever)
        self._t.daemon = True
        self._t.start()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            except Exception:
                return e.code, None

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def seeded(tmp_path):
    home = str(tmp_path / ".binggupack")
    os.makedirs(home)
    ledger = os.path.join(home, "ledger.sqlite")
    _seed(ledger, home)
    return home, ledger


@pytest.fixture
def studio(seeded):
    home, ledger = seeded
    s = _Studio(ledger)
    try:
        yield s, home, ledger
    finally:
        s.close()


def _detail_url(s, rid):
    return s.get("api/approval/" + urllib.parse.quote(rid, safe=""))


# ════════════════════════ list / effective state ════════════════════════
def test_approval_list_effective_states(seeded):
    home, ledger = seeded
    snap = av.collect_approval_list_snapshot(ledger, now=NOW)
    states = {it["request_id"]: it["effective_state"] for it in snap["items"]}
    assert states["req_pending01"] == "pending"
    assert states["req_approved1"] == "approved"
    assert states["req_consumed1"] == "consumed"
    assert states["req_rejected1"] == "rejected"
    assert states["req_expired01"] == "expired"
    assert snap["summary_counts"]["consumed"] == 1 and snap["summary_counts"]["expired"] == 1


def test_approval_list_filters_and_paginates(seeded):
    home, ledger = seeded
    assert av.collect_approval_list_snapshot(ledger, state="consumed", now=NOW)["total"] == 1
    assert av.collect_approval_list_snapshot(ledger, operation="hosted_bundle", now=NOW)["total"] == 2
    p0 = av.collect_approval_list_snapshot(ledger, limit=2, offset=0, now=NOW)
    p1 = av.collect_approval_list_snapshot(ledger, limit=2, offset=2, now=NOW)
    assert len(p0["items"]) == 2 and p0["total"] == 5
    assert set(i["request_id"] for i in p0["items"]).isdisjoint(i["request_id"] for i in p1["items"])


def test_approval_list_deterministic_order(seeded):
    home, ledger = seeded
    a = [i["request_id"] for i in av.collect_approval_list_snapshot(ledger, now=NOW)["items"]]
    b = [i["request_id"] for i in av.collect_approval_list_snapshot(ledger, now=NOW)["items"]]
    assert a == b
    # created_at DESC → expired(NOW-1000) 는 가장 오래됨 → 마지막
    assert a[-1] == "req_expired01"


# ════════════════════════ detail / exact-ID ════════════════════════
def test_approval_detail_exact_full_request_id(seeded):
    home, ledger = seeded
    d = av.collect_approval_detail_snapshot(ledger, "req_approved1", now=NOW)
    assert d["request"]["request_id"] == "req_approved1"
    assert d["request"]["effective_state"] == "approved"
    assert d["request"]["display_id"] == _id8("req_approved1")


def test_approval_detail_rejects_short_or_fuzzy_id(studio):
    s, home, ledger = studio
    assert _detail_url(s, _id8("req_approved1"))[0] == 404      # id8
    assert _detail_url(s, "req_approved")[0] == 404             # prefix
    assert _detail_url(s, "approved1")[0] == 404                # suffix
    assert _detail_url(s, "req_approved1")[0] == 200            # exact


def test_approval_detail_missing_404(studio):
    s, home, ledger = studio
    assert _detail_url(s, "req_nonexistent")[0] == 404
    assert s.get("api/approval/" + urllib.parse.quote("req\x00evil", safe=""))[0] == 400
    assert s.get("api/approval/" + urllib.parse.quote("x" * 400, safe=""))[0] == 400


def test_approval_detail_never_exposes_nonce(seeded):
    home, ledger = seeded
    appr = ta.find_approve(home, "req_approved1")
    for rid in ("req_approved1", "req_consumed1"):
        blob = json.dumps(av.collect_approval_detail_snapshot(ledger, rid, now=NOW), ensure_ascii=False)
        assert appr["approval_nonce"] not in blob
        assert "NONCE_MUST_NOT_LEAK" not in blob
        assert "approval_nonce" not in blob


def test_approval_detail_never_exposes_absolute_path(seeded):
    home, ledger = seeded
    for rid in ("req_approved1", "req_consumed1", "req_rejected1"):
        blob = json.dumps(av.collect_approval_detail_snapshot(ledger, rid, now=NOW), ensure_ascii=False)
        assert home not in blob and ".binggupack" not in blob
        assert ledger not in blob


def test_approval_detail_receipt_is_sanitized(seeded):
    home, ledger = seeded
    d = av.collect_approval_detail_snapshot(ledger, "req_consumed1", now=NOW)
    cv = d["consumption"]
    assert cv["state"] == "consumed" and cv["receipt_available"]
    rc = cv["receipt"]
    assert "actor" not in rc and "approval_nonce" not in rc
    assert rc["operation"] == "hosted_bundle" and rc["decision_id"] == "dec_abc"
    assert "NONCE_MUST_NOT_LEAK" not in json.dumps(cv, ensure_ascii=False)


def test_approval_receipt_links_exact_memory_id(seeded):
    home, ledger = seeded
    d = av.collect_approval_detail_snapshot(ledger, "req_consumed1", now=NOW)
    nodes = {n["node_id"]: n for n in d["consumption"]["receipt"]["nodes"]}
    assert nodes["node:CONV:aaaa1111"]["dangling"] is False   # 실재 → 링크
    assert nodes["node:CONV:aaaa1111"]["display_id"] == _id8("node:CONV:aaaa1111")
    assert nodes["node:CONV:missing99"]["dangling"] is True   # 부재 → 자동보정 0


# ════════════════════════ review 무결성 ════════════════════════
def test_approval_review_digest_mismatch_hides_items(seeded):
    home, ledger = seeded
    p = ta._review_path(home, "req_approved1")
    data = json.load(open(p, encoding="utf-8"))
    data["payload_digest"] = "TAMPERED"
    json.dump(data, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    d = av.collect_approval_detail_snapshot(ledger, "req_approved1", now=NOW)
    assert d["review"]["available"] is False and d["review"]["integrity"] == "mismatch"
    assert "items" not in d["review"]


def test_approval_review_operation_mismatch_hides_items(seeded):
    home, ledger = seeded
    p = ta._review_path(home, "req_approved1")
    data = json.load(open(p, encoding="utf-8"))
    data["operation"] = "deprecate"
    json.dump(data, open(p, "w", encoding="utf-8"), ensure_ascii=False)
    d = av.collect_approval_detail_snapshot(ledger, "req_approved1", now=NOW)
    assert d["review"]["available"] is False and d["review"]["integrity"] == "mismatch"


def test_approval_review_symlink_is_rejected(seeded, tmp_path):
    home, ledger = seeded
    p = ta._review_path(home, "req_approved1")
    os.remove(p)
    target = tmp_path / "evil.json"
    target.write_text(json.dumps({"request_id": "req_approved1", "operation": "save_candidate",
                                  "payload_digest": "digestA", "items": []}), encoding="utf-8")
    try:
        os.symlink(str(target), p)
    except (OSError, NotImplementedError):
        pytest.skip("symlink 생성 불가(권한)")
    d = av.collect_approval_detail_snapshot(ledger, "req_approved1", now=NOW)
    assert d["review"]["available"] is False and d["review"]["integrity"] == "mismatch"


def test_approval_review_oversize_is_rejected(seeded):
    home, ledger = seeded
    p = ta._review_path(home, "req_approved1")
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"request_id": "req_approved1", "operation": "save_candidate",
                            "payload_digest": "digestA", "items": [], "pad": "x" * (300 * 1024)}))
    d = av.collect_approval_detail_snapshot(ledger, "req_approved1", now=NOW)
    assert d["review"]["available"] is False and d["review"]["integrity"] == "mismatch"


def test_approval_review_matched_shows_items(seeded):
    home, ledger = seeded
    d = av.collect_approval_detail_snapshot(ledger, "req_approved1", now=NOW)
    assert d["review"]["available"] and d["review"]["integrity"] == "matched"
    assert len(d["review"]["items"]) >= 1


# ════════════════════════ timeline / state semantics ════════════════════════
def test_approval_timeline_matches_existing_semantics(seeded):
    home, ledger = seeded
    d = av.collect_approval_detail_snapshot(ledger, "req_approved1", now=NOW)
    kinds = [e["kind"] for e in d["timeline"]]
    assert kinds[0] == "request_created" and "approved" in kinds
    dr = av.collect_approval_detail_snapshot(ledger, "req_rejected1", now=NOW)
    assert "rejected" in [e["kind"] for e in dr["timeline"]]
    # nonce/digest 미노출(timeline 원소에 허용 필드만)
    for e in d["timeline"]:
        assert set(e.keys()) <= {"kind", "at", "channel"}


def test_consumed_state_not_overwritten_by_expiry(seeded):
    home, ledger = seeded
    # now 를 아주 먼 미래로 → expiry 경과해도 consumed 유지
    d = av.collect_approval_detail_snapshot(ledger, "req_consumed1", now=NOW + 10_000_000)
    assert d["request"]["effective_state"] == "consumed"


def test_approved_after_request_expiry_still_approved(tmp_path):
    # request-row 만료 후 fresh TTL 로 승인된 건은 실제 소비 게이트(verify_event=approve event expires_at)에서
    # 소비 가능하므로 effective_state=approved(request-row 만료로 'expired' 오표기 금지 — reviewer Q5 blocker).
    home = str(tmp_path / ".binggupack")
    os.makedirs(home)
    ledger = os.path.join(home, "ledger.sqlite")
    db = open_accept(ledger)
    con = db.con
    ta.upsert_request(con, "req_late01", ta.PROTOCOL_VERSION, "pair", "digestLate", LID, "pair", NOW - 1000, 900, 32)
    ta.mint_approval(home, ta.get_request(con, "req_late01"), 900, NOW)   # approve expires NOW+900 (fresh)
    db.con.commit()
    db.close()
    d = av.collect_approval_detail_snapshot(ledger, "req_late01", now=NOW)
    assert d["request"]["effective_state"] == "approved"   # 소비 가능 → NOT expired
    assert d["request"]["expired"] is True                 # request-row expiry flag 는 사실대로 유지


def test_approved_unconsumed_guidance_is_safe(seeded):
    home, ledger = seeded
    d = av.collect_approval_detail_snapshot(ledger, "req_approved1", now=NOW)
    # commands 는 고정 CLI 4종만(원 mutation 추측 0)
    assert set(d["commands"]) == {"show", "approve", "reject", "revoke"}
    assert d["commands"]["approve"] == "binggu approval approve req_approved1"
    assert d["consumption"]["receipt_available"] is False


# ════════════════════════ UI 정적 계약 ════════════════════════
def test_studio_approval_actions_copy_commands_only():
    js = open(os.path.join(_STATIC, "app.js"), encoding="utf-8").read()
    # approval UI 는 copyButton 으로 CLI 명령만. approve/reject/revoke 실행 fetch 없음.
    assert "j.commands.approve" in js and "j.commands.reject" in js
    # POST/PUT/DELETE mutation fetch 없음
    for banned in ("method: \"POST\"", "method:\"POST\"", "method: 'POST'"):
        assert banned not in js, banned


def test_studio_approval_has_no_mutation_fetch():
    js = open(os.path.join(_STATIC, "app.js"), encoding="utf-8").read()
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                   ".submit(", "XMLHttpRequest"):
        assert banned not in js, banned
    # fetch 는 getJSON(GET) 단일 경로만
    assert js.count("fetch(") == 1


def test_studio_approval_refresh_is_read_only(studio):
    s, home, ledger = studio
    before = _snap_files(home)
    for _ in range(5):
        s.get("api/approvals?state=all")
        _detail_url(s, "req_consumed1")
        _detail_url(s, "req_approved1")
        s.get("api/approvals?state=bogus")            # invalid
        _detail_url(s, "req_nonexistent")             # missing
    assert _snap_files(home) == before, "조회가 파일을 변경했다"


def test_no_ledger_approval_center_creates_nothing(tmp_path):
    home = str(tmp_path / ".binggupack")
    os.makedirs(home)
    ledger = os.path.join(home, "ledger.sqlite")
    s = _Studio(ledger)
    try:
        st, j = s.get("api/approvals?state=all")
        assert st == 200 and j["total"] == 0 and j["items"] == []
        assert _detail_url(s, "req_whatever")[0] == 404
    finally:
        s.close()
    assert not os.path.exists(ledger)
    assert sorted(os.listdir(home)) == []


# ════════════════════════ packaging ════════════════════════
def test_wheel_approval_center_assets_present():
    import binggupack.studio.approval_view as m
    assert m.SCHEMA_VERSION == 1
    html = open(os.path.join(_STATIC, "index.html"), encoding="utf-8").read()
    assert 'data-view="approvals"' in html


def test_external_cwd_approval_api_smoke(tmp_path):
    home = str(tmp_path / ".binggupack")
    os.makedirs(home)
    ledger = os.path.join(home, "ledger.sqlite")
    _seed(ledger, home)
    ext = str(tmp_path / "ext")
    os.makedirs(ext)
    e = dict(os.environ)
    e["PYTHONUTF8"] = "1"
    e["PYTHONIOENCODING"] = "utf-8"
    e["BINGGU_HOME"] = home
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "binggu.py"), "--ledger", ledger,
         "studio", "--no-open", "--port", "0"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        encoding="utf-8", errors="replace", env=e, cwd=ext)
    try:
        url = None
        deadline = time.time() + 20
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            if line.startswith("URL:"):
                url = line.split("URL:", 1)[1].strip()
                break
        assert url, "URL 미출력"
        with urllib.request.urlopen(url + "api/approvals?state=all", timeout=5) as r:
            assert r.status == 200 and json.loads(r.read())["total"] == 5
        with urllib.request.urlopen(url + "api/approval/" +
                                    urllib.parse.quote("req_consumed1", safe=""), timeout=5) as r:
            assert r.status == 200 and json.loads(r.read())["request"]["effective_state"] == "consumed"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    assert proc.poll() is not None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
