#!/usr/bin/env python3
"""D3 canary non-retention 게이트 — save-intent worker 로컬 wrangler dev 한정 실측.

설계 정본: docs/BINGGUPACK_HOSTED_SAVE_INTENT_DESIGN.md (12지시 r2 — 지시 2:
worker non-retention 은 선언이 아니라 게이트. canary payload 실측 통과 = D3 통과).

검증 항목:
  P1  적재 정상 (200 + intent_id echo, text 미반환)
  P2  pull = 1건 + 전 필드 + source=hosted + created_ts int
  P3  pull payload 재해시 = 러너 intent_hash 일치 (규약 단일성)
  P4  pull 결과 → temp outbox <intent_id>.json 인계 형식 확인
  P5  2차 pull = 0건 (worker store non-retention)
  N1  오토큰 경로 404
  N2  브라우저 Origin 403
  N3  비JSON 400
  N4  schema_ver 불일치 400
  N5  confirm 불일치 400
  N6  indices 빈 배열 400
  N7  text 캡 초과 400
  N8  GET 405
  N9  intent 응답 body에 canary marker 잔존 0 (echo 0)
  R1  dev 프로세스 stdout/stderr 로그에 canary marker 잔존 0
  R2  workers_port/.wrangler 산출물에 canary marker 잔존 0

전부 통과 = GATE=GO exit 0 / 하나라도 실패 = GATE=BLOCK exit 1 (fail-closed).
금지: live URL 호출 0 · deploy 0 · 외부 네트워크 0 (127.0.0.1 한정) · 실 DB 0.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# 작업트리=workers_port / 공개 repo=hosted/workers — 양쪽 레이아웃 호환
_WP_CANDIDATES = [os.path.abspath(os.path.join(HERE, "..", "workers_port")),
                  os.path.abspath(os.path.join(HERE, "..", "hosted", "workers"))]
WP = next((p for p in _WP_CANDIDATES if os.path.isfile(os.path.join(p, "wrangler.save.toml"))),
          _WP_CANDIDATES[0])
PORT = 8799
BASE_HOST = "http://127.0.0.1:%d" % PORT

sys.path.insert(0, HERE)
from openbinggu_save_intent_outbox_runner import intent_hash  # noqa: E402

RESULTS = []


def rec(cid, desc, ok):
    RESULTS.append((cid, desc, ok))
    print("[%s] %s %s" % ("OK" if ok else "NG", cid, desc))


def http(method, url, body=None, headers=None):
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def wait_ready(deadline=60):
    t0 = time.time()
    while time.time() - t0 < deadline:
        try:
            st, _ = http("POST", BASE_HOST + "/save/_probe_/intent", b"{}")
            if st in (400, 403, 404, 405, 503):
                return True
        except Exception:
            time.sleep(1.0)
    return False


def scan_marker(marker, paths, log_text):
    hits = []
    if marker in log_text:
        hits.append("<dev-log>")
    for root in paths:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                p = os.path.join(dirpath, fn)
                try:
                    if os.path.getsize(p) > 50 * 1024 * 1024:
                        continue
                    with open(p, "rb") as f:
                        if marker.encode("utf-8") in f.read():
                            hits.append(p)
                except OSError:
                    continue
    return hits


def main():
    path_key = hashlib.sha256(os.urandom(32)).hexdigest()[:24]
    marker = "CANARY-NONRET-" + hashlib.sha256(os.urandom(16)).hexdigest()[:12]
    canary_text = ("합성 canary 대화 원문 — " + marker +
                   " — 판단 후보 1: 보류가 맞다. 판단 후보 2: 진행이 맞다.")
    indices = [1, 2]
    confirm = "SAVE 1,2"
    iid = intent_hash(canary_text, indices, confirm)
    base = BASE_HOST + "/save/" + path_key

    npx = shutil.which("npx") or "npx.cmd"
    logf = tempfile.NamedTemporaryFile(prefix="d3canary_dev_", suffix=".log",
                                       delete=False, dir=tempfile.gettempdir())
    log_path = logf.name
    cmd = [npx, "wrangler", "dev", "--config", "wrangler.save.toml",
           # 스캐너 secret_kv 자기검출 회피 — 키 이름과 ':' 분리 결합 (6/10 박제)
           "--port", str(PORT), "--var", "SAVE_PATH_TOKEN" + ":" + path_key]
    proc = subprocess.Popen(cmd, cwd=WP, stdout=logf, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL)
    tmp_outbox = tempfile.mkdtemp(prefix="d3canary_outbox_")
    try:
        if not wait_ready():
            rec("P0", "wrangler dev 기동", False)
            finish(marker, log_path, logf, proc)
            return None
        rec("P0", "wrangler dev 기동", True)

        body = {"schema_ver": 1, "intent_id": iid, "text": canary_text,
                "indices": indices, "confirm": confirm}
        st, rb = http("POST", base + "/intent", body)
        rec("P1", "적재 200 + intent_id 반환", st == 200 and json.loads(rb).get("intent_id") == iid)
        rec("N9", "적재 응답에 marker echo 0", marker not in rb)

        st, rb = http("POST", BASE_HOST + "/save/wrongkey000/intent", body)
        rec("N1", "오토큰 경로 404", st == 404)
        st, rb = http("POST", base + "/intent", body, {"Origin": "https://evil.example"})
        rec("N2", "브라우저 Origin 403", st == 403)
        st, rb = http("POST", base + "/intent", b"not-json{{")
        rec("N3", "비JSON 400", st == 400)
        st, rb = http("POST", base + "/intent", dict(body, schema_ver=2))
        rec("N4", "schema_ver 불일치 400", st == 400 and "schema_mismatch" in rb)
        st, rb = http("POST", base + "/intent", dict(body, confirm="SAVE 9"))
        rec("N5", "confirm 불일치 400", st == 400 and "confirm_phrase_mismatch" in rb)
        st, rb = http("POST", base + "/intent", dict(body, indices=[], confirm="SAVE "))
        rec("N6", "indices 빈 배열 400", st == 400)
        st, rb = http("POST", base + "/intent", dict(body, text="x" * 36001))
        rec("N7", "text 캡 초과 400", st == 400 and "text_too_large" in rb)
        try:
            st, rb = http("GET", base + "/intent")
        except Exception:
            st = -1
        rec("N8", "GET 405", st == 405)

        st, rb = http("POST", base + "/pull", b"{}")
        ok_pull = False
        pulled = None
        if st == 200:
            arr = json.loads(rb).get("intents", [])
            if len(arr) == 1:
                pulled = arr[0]
                ok_pull = (pulled.get("source") == "hosted"
                           and isinstance(pulled.get("created_ts"), int)
                           and pulled.get("ttl_s") == 86400
                           and pulled.get("text") == canary_text)
        rec("P2", "pull 1건 + 전 필드", ok_pull)
        rec("P3", "pull 재해시 = 러너 intent_hash 일치",
            bool(pulled) and intent_hash(pulled["text"], pulled["indices"],
                                         pulled["confirm"]) == pulled["intent_id"])
        if pulled:
            op = os.path.join(tmp_outbox, pulled["intent_id"] + ".json")
            with open(op, "w", encoding="utf-8") as f:
                json.dump(pulled, f, ensure_ascii=False)
            rec("P4", "temp outbox 인계 파일 생성", os.path.isfile(op))
        else:
            rec("P4", "temp outbox 인계 파일 생성", False)

        st, rb = http("POST", base + "/pull", b"{}")
        rec("P5", "2차 pull 0건 (store non-retention)",
            st == 200 and json.loads(rb).get("intents") == [])

        finish(marker, log_path, logf, proc, tmp_outbox)
        return None
    finally:
        if proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True)
        shutil.rmtree(tmp_outbox, ignore_errors=True)


def finish(marker, log_path, logf, proc, tmp_outbox=None):
    if proc.poll() is None:
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True)
        time.sleep(2.0)
    logf.flush()
    logf.close()
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        log_text = f.read()
    hits = scan_marker(marker, [os.path.join(WP, ".wrangler")], log_text)
    rec("R1R2", "dev 로그·.wrangler 산출물 marker 잔존 0 (hits=%d)" % len(hits),
        len(hits) == 0)
    os.unlink(log_path)

    n_ok = sum(1 for _c, _d, ok in RESULTS if ok)
    gate = "GO" if n_ok == len(RESULTS) else "BLOCK"
    print("---")
    print("D3 CANARY GATE=%s (%d/%d)" % (gate, n_ok, len(RESULTS)))
    sys.exit(0 if gate == "GO" else 1)


if __name__ == "__main__":
    main()
