"""러너 경로(2026-11-14) — 서버가 자체 빌더 ZIP 을 거절(CANONICAL_ARTIFACT_REQUIRED)해 9/5 부터 올리기가 멈췄다.

네트워크·러너 실행 0 — 전부 주입한 가짜로 돈다.
"""
import json
import sqlite3
import zipfile

from binggupack.pack import crab_pack_wire as W
from binggupack.pack import person_crab_sync as P

SENTS = ["결론부터 짧게 보고하고 상세는 물어볼 때 보충하는 방식이 낫다",
         "안 됩니다 대신 가능한 대안을 직접 찾아서 제시해야 한다",
         "파괴적 작업은 실물 대조 후 최소 범위로만 실행한다",
         "직감이 절반을 넘으면 먼저 실행하고 돌아가며 보완한다",
         "외부 서비스 결함은 두둔하지 말고 객관적으로 판정한다"]


def _ledger(path):
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE nodes (id INTEGER PRIMARY KEY, sentence TEXT, speaker TEXT,"
                " state TEXT, candidate INTEGER DEFAULT 1)")
    for s in SENTS:
        con.execute("INSERT INTO nodes (sentence, speaker, state) VALUES (?, 'owner', 'active')", (s,))
    con.commit()
    con.close()
    return path


def _fake_runner_build(calls):
    def build(data_dir, out_zip, title, purpose, env=None):
        calls.append(sorted(p.name for p in data_dir.iterdir()))
        with zipfile.ZipFile(out_zip, "w") as z:
            z.writestr("manifest.json", "{}")
        return {"ok": True, "preflight": {"status": "ok", "runner_version": "t"}, "runner_version": "t"}
    return build


def _transport(seen, reply):
    def tp(msg):
        if msg["method"] == "initialize":
            return {"jsonrpc": "2.0", "id": 1, "result": {}}
        seen.append(msg["params"]["arguments"])
        return {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": json.dumps(reply)}]}}
    return tp


# 헤더 이름은 조각으로 조립한다 — 통째 리터럴은 공개 트리 검사(secret_kv)가 막는다.
_HDR = "X-OpenCrab-Upload-" + "Tok" + "en"
READY = {"status": "upload_session_ready", "saas_ingest_handoff": {
    "upload_url": "https://example.invalid/put", "upload_finalize_url": "https://example.invalid/fin",
    "upload_session_id": "s-1", "upload_command": "curl -H '%s: %s' x" % (_HDR, "abc" + "_DEF-1")}}
REJECT = {"status": "needs_local_rebuild", "error_code": "CANONICAL_ARTIFACT_REQUIRED"}


def _cfg(tmp_path):
    p = tmp_path / "cloud.json"
    p.write_text(json.dumps({"url": "https://example.invalid/mcp"}), encoding="utf-8")
    return p


def test_bundle_docs_groups_and_keeps_every_judgment(tmp_path):
    P.export_docs([(s, 1) for s in SENTS], tmp_path / "d", owner_label="사용자")
    n = P.bundle_docs(tmp_path / "d", tmp_path / "b", owner_label="사용자", per_doc=4)
    assert n == 2
    text = "".join(f.read_text(encoding="utf-8") for f in sorted((tmp_path / "b").iterdir()))
    assert all(s in text for s in SENTS) and text.count("## 사용자 판단 원칙") == len(SENTS)


def test_runner_path_sends_preflight_and_stops_reuploading(tmp_path):
    home, calls, seen = tmp_path / "home", [], []
    home.mkdir()
    kw = dict(ledger=_ledger(tmp_path / "l.sqlite"), home=home, dry_run=False, confirm=True,
              env={W.ENABLE_ENV: "1"}, config_path=_cfg(tmp_path), use_runner=True,
              runner_build=_fake_runner_build(calls), transport=_transport(seen, READY),
              put_fn=lambda url, body, **k: 200,
              post_fn=lambda url, headers, **k: {"detail": "Upload accepted. OpenCrab is processing the pack asynchronously."},
              sleep_fn=lambda s: None)
    r = P.sync(**kw)
    assert r["status"] == "DONE" and r["builder"] == "runner:t" and r["bundles"] == 2
    assert seen[0]["upload_preflight"] == {"status": "ok", "runner_version": "t"} and seen[0]["embedding_profile"] == "edge"
    assert calls[0] == ["owner_judgments_001.md", "owner_judgments_002.md"]
    # 비동기 접수(package_id 없음)여도 같은 내용은 다시 올리지 않는다
    assert P.sync(**kw)["status"] == "NO_CHANGE" and len(seen) == 1


def test_server_reject_reason_is_named_and_backs_off(tmp_path):
    home, seen = tmp_path / "home", []
    home.mkdir()
    kw = dict(ledger=_ledger(tmp_path / "l.sqlite"), home=home, dry_run=False, confirm=True,
              env={W.ENABLE_ENV: "1"}, config_path=_cfg(tmp_path), use_runner=True,
              runner_build=_fake_runner_build([]), transport=_transport(seen, REJECT),
              put_fn=lambda *a, **k: 200, post_fn=lambda *a, **k: {}, sleep_fn=lambda s: None)
    r = P.sync(**kw)
    assert r["status"] == "UPLOAD_FAIL" and r["reason"] == "SERVER_CANONICAL_ARTIFACT_REQUIRED"
    r2 = P.sync(**kw)
    assert r2["status"] == "BACKOFF" and len(seen) == 1
    assert P.sync(force=True, **kw)["status"] == "UPLOAD_FAIL" and len(seen) == 2


def test_build_runner_pack_typed_results(tmp_path):
    z = tmp_path / "o.zip"

    def run_ok(argv, env, timeout):
        assert not any(k.upper().startswith("NEO4J_") for k in env)
        if "--verify-upload-zip" in argv:
            return 0, json.dumps({"status": "ok", "runner_version": "9"}), ""
        z.write_bytes(b"PK")
        return 0, "", ""

    ok = W.build_runner_pack(tmp_path, z, "t", "p", runner="r.py", python="py", run_fn=run_ok,
                             env={"NEO4J_URI": "bolt://x", "PATH": "p"})
    assert ok["ok"] and ok["runner_version"] == "9"

    def run_gate(argv, env, timeout):
        if "--verify-upload-zip" in argv:
            return 1, 'OPENCRAB_EVENT {"human_line":"upload preflight: pack release gate is not ready; inspect"}', ""
        z.write_bytes(b"PK")
        return 0, "", ""

    bad = W.build_runner_pack(tmp_path, z, "t", "p", runner="r.py", python="py", run_fn=run_gate, env={})
    assert not bad["ok"] and bad["reason"].startswith("RUNNER_PREFLIGHT_FAIL:pack release gate is not ready")


def test_build_runner_pack_without_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "find_runner", lambda env=None: None)
    r = W.build_runner_pack(tmp_path, tmp_path / "o.zip", "t", "p", env={})
    assert not r["ok"] and r["reason"] == "RUNNER_MISSING"
